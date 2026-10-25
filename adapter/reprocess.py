"""Reprocessing after a domain profile change (relationship analysis, E26).

A changed profile (new relation or entity types, a direction rule, identifier
patterns) only applies to what is extracted after it. The graph records which
profile each document was extracted with (graph_service /documents/profile);
this runs those of an older or unknown profile through extraction again.

The box keeps no raw file, so each document is fetched again -- from the
ViVeSecBox over the ws-fs channel as the administrator who started the run, or,
on a development box without that channel, from ADAPTER_REPROCESS_SOURCE_DIR
(a mirror of the drives) -- and handed to the graph forwarder exactly like a
synced file (its ACL is evaluated at send time). One document is fetched only
when the forwarder and the graph queue are empty, so a run never piles raw
files up in memory and yields to normal syncing. The graph re-resolves a
document's people to the ids they had, so review decisions survive.
"""
import threading
import time


class ReprocessError(Exception):
    """The run cannot start (no file source, already running)."""


class Reprocessor:
    def __init__(self, graph_client, forward, fetch, idle_wait=2.0):
        """fetch(user, path) -> bytes, or None when the file is gone; raises
        ReprocessError when the source itself is unavailable."""
        self.graph = graph_client
        self.forward = forward
        self.fetch = fetch
        self.idle_wait = idle_wait
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.run = None
        self.thread = None

    def running(self):
        with self.lock:
            return bool(self.run and self.run["state"] == "running")

    def status(self, visible=None):
        """The current or last run; the file being read only when `visible(path)`."""
        with self.lock:
            if not self.run:
                return {"state": "idle"}
            out = {k: v for k, v in self.run.items() if k != "current"}
            current = self.run.get("current")
        if current and (visible is None or visible(current)):
            out["current"] = current
        return out

    def start(self, user, docs, profile, path_prefix=""):
        with self.lock:
            if self.run and self.run["state"] == "running":
                raise ReprocessError("a reprocessing run is already going")
            self.cancel_event.clear()
            self.run = {"state": "running", "started_by": user, "started": time.time(),
                        "profile": profile, "path_prefix": path_prefix or None,
                        "total": len(docs), "done": 0, "sent": 0, "missing": 0, "failed": 0,
                        "current": None, "error": None, "finished": None}
            self.thread = threading.Thread(target=self._loop, args=(user, list(docs)),
                                           name="graph-reprocess", daemon=True)
            self.thread.start()
        return self.status()

    def cancel(self):
        self.cancel_event.set()

    def wait(self, timeout=None):
        thread = self.thread
        if thread is not None:
            thread.join(timeout)
        return not (thread and thread.is_alive())

    def _update(self, **changes):
        with self.lock:
            self.run.update(changes)

    def _idle(self):
        """Wait until the forwarder and the graph queue are empty; False on cancel."""
        while not self.cancel_event.is_set():
            if not self.forward.stats()["pending"]:
                try:
                    status = self.graph.post("/ingest/status", {})
                except Exception:  # noqa: BLE001 -- the graph restarting: keep waiting
                    status = {"queued": 1}
                if not status.get("queued"):
                    return True
            self.cancel_event.wait(self.idle_wait)
        return False

    def _loop(self, user, docs):
        state = "done"
        for doc in docs:
            if not self._idle():
                state = "cancelled"
                break
            path = doc["source_path"]
            self._update(current=path)
            try:
                raw = self.fetch(user, path)
            except ReprocessError as e:
                self._update(error=str(e))
                state = "failed"
                break
            except Exception as e:  # noqa: BLE001 -- one unreadable file must not stop the run
                raw = None
                self._update(error="%s: %s" % (path.rsplit("/", 1)[-1], e))
                with self.lock:
                    self.run["failed"] += 1
                    self.run["done"] += 1
                continue
            with self.lock:
                if raw is None:
                    self.run["missing"] += 1
                elif self.forward.file(path, doc["corpus_id"], doc["doc_id"], raw):
                    self.run["sent"] += 1
                else:
                    self.run["failed"] += 1  # not forwardable (type, size, queue)
                self.run["done"] += 1
        if state == "done" and self.cancel_event.is_set():
            state = "cancelled"
        self._update(state=state, current=None, finished=time.time())
