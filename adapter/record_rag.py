"""The RAG copy of structured records (E29): the free text of a log or ODIN entry.

A structured import (E28) writes records into the graph only. A source the
profile marks `rag` (bv: the intelligence log, ODIN) also has its records' open
text searchable and citable. The graph keeps that text with each open record
(never a protected part, never a source without `rag`) and lists it per folder
(/import/rag/list, /import/rag/get); this module keeps the RAG in line with it:

  * it lists the folder's records in the graph and in the RAG (/index/records)
    by path and tag, writes the new and changed ones (/ingest, text with tag)
    and drops what the graph no longer lists (/index/drop/tree);
  * each written record carries the folder's flattened ACL (as now), the folder
    it follows (`follows`: a later ACL push of the folder reaches it, as in the
    graph) and its own rule (`require`, e.g. its institution's group) as an AND
    gate: visible only to a caller both admit; an empty rule admits nobody;
  * a folder ACL that changed while a batch was written is pushed again, so the
    record never keeps a wider rule than the folder's current one;
  * a folder delete drops its records with it (the RAG's drop of the tree).

It runs after an import and when its status is asked, one folder at a time on
its own thread, idempotently (a second run of an unchanged folder writes
nothing); a failed run is retried a few times and reported in the status.
"""
import sys
import threading
import time
from collections import OrderedDict, deque

RECORDS_DIR = "~records"
BATCH = 50
PAGE = 1000
RETRIES = (15, 60, 300)
MAX_STATUS = 200


class RecordRag:
    def __init__(self, graph_post, rag_post, flat_acl, corpus_of, tenant_id="default",
                 acl_changed=None, retries=RETRIES):
        self.graph_post = graph_post
        self.rag_post = rag_post
        self.flat_acl = flat_acl
        self.corpus_of = corpus_of
        self.tenant_id = tenant_id
        self.acl_changed = acl_changed or (lambda folder: None)
        self.retries = retries
        self.cond = threading.Condition()
        self.queue = deque()
        self.states = OrderedDict()
        self.thread = None

    # -- requests ---------------------------------------------------------------------

    def request(self, folder):
        """Align `folder` soon (on the worker thread); a pending request stands."""
        folder = folder.rstrip("/")
        with self.cond:
            state = self.states.get(folder) or {}
            if state.get("state") not in ("pending", "running"):
                self._set(folder, dict(state, state="pending", tries=0))
                self.queue.append(folder)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="record-rag", daemon=True)
                self.thread.start()
            self.cond.notify_all()

    def status(self, folder):
        with self.cond:
            return dict(self.states.get(folder.rstrip("/")) or {"state": "none"})

    def wait(self, folder, timeout):
        """The status once `folder` is no longer pending or running (tests)."""
        end = time.time() + timeout
        with self.cond:
            while (self.states.get(folder.rstrip("/")) or {}).get("state") in ("pending",
                                                                              "running"):
                left = end - time.time()
                if left <= 0:
                    break
                self.cond.wait(left)
        return self.status(folder)

    def _set(self, folder, state):
        self.states.pop(folder, None)
        self.states[folder] = state
        while len(self.states) > MAX_STATUS:
            self.states.popitem(last=False)
        self.cond.notify_all()

    def _loop(self):
        while True:
            with self.cond:
                while not self.queue:
                    self.cond.wait()
                folder = self.queue.popleft()
                state = dict(self.states.get(folder) or {}, state="running")
                self._set(folder, state)
            try:
                out = self.sync(folder)
                with self.cond:
                    self._set(folder, dict(out, state="done", at=round(time.time(), 1)))
            except Exception as e:  # noqa: BLE001 - the worker must survive a failed run
                tries = state.get("tries", 0)
                sys.stderr.write("[adapter] record rag: %s failed (%s)\n" % (folder, e))
                with self.cond:
                    retry = tries < len(self.retries)
                    self._set(folder, dict(state, state="pending" if retry else "failed",
                                           tries=tries + 1, error=str(e)[:300],
                                           at=round(time.time(), 1)))
                if retry:
                    threading.Timer(self.retries[tries], self._requeue, (folder,)).start()

    def _requeue(self, folder):
        with self.cond:
            if (self.states.get(folder) or {}).get("state") == "pending" \
                    and folder not in self.queue:
                self.queue.append(folder)
                self.cond.notify_all()

    # -- one folder -------------------------------------------------------------------

    def _pages(self, post, path, body, key="documents"):
        out, after = {}, ""
        while True:
            res = post(path, dict(body, after=after, limit=PAGE))
            for item in res.get(key) or []:
                out[item["path"]] = item["tag"]
            after = res.get("next")
            if not after:
                return out

    def sync(self, folder):
        """Align the RAG copy of `folder`'s records with the graph; the counts."""
        folder = folder.rstrip("/")
        corpus_id = self.corpus_of(folder)
        root = "%s/%s" % (folder, RECORDS_DIR)
        graph = self._pages(self.graph_post, "/import/rag/list",
                            {"corpus_id": corpus_id, "folder": folder})
        indexed = self._pages(self.rag_post, "/index/records",
                              {"corpus_id": corpus_id, "prefix": root})
        stale = sorted(p for p in indexed if p not in graph)
        for path in stale:
            self.rag_post("/index/drop/tree", {"corpus_id": corpus_id, "tenant_id": self.tenant_id,
                                               "path": path, "keep_exact": False})
        changed = sorted(p for p, tag in graph.items() if indexed.get(p) != tag)
        written = 0
        for start in range(0, len(changed), BATCH):
            flat = self.flat_acl(folder)
            items = self.graph_post("/import/rag/get", {"corpus_id": corpus_id, "folder": folder,
                                                        "paths": changed[start:start + BATCH]})
            for item in items.get("documents") or []:
                if not item["path"].startswith(root + "/"):
                    continue  # never outside the folder's records
                acl = dict(flat, follows=folder)
                if item.get("require") is not None:
                    acl.update(require=item["require"],
                               require_classification=item.get("require_classification") or 0)
                self.rag_post("/ingest", {"corpus_id": corpus_id, "tenant_id": self.tenant_id,
                                          "path": item["path"], "title": item["title"],
                                          "text": item["text"],
                                          "metadata": {"tag": item["tag"], "acl": acl}})
                written += 1
            if self.flat_acl(folder) != flat:
                # The folder's rule changed meanwhile: push it over what was written.
                self.acl_changed(folder)
        return {"records": len(graph), "written": written, "removed": len(stale),
                "unchanged": len(graph) - len(changed)}
