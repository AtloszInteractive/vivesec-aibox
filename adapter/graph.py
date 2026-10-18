"""Relationship analysis (E26): the adapter's link to the internal graph_service.

The graph service is internal like the RAG; the adapter decides the caller's
scope and access and sends them with every query. Files reach the graph the
same way they reach the RAG: after the RAG committed a synced file, its bytes
are handed to a forwarder that delivers them to the graph's extraction queue
in the background, so the ViVeSecBox sync never waits for the graph.

Forwarding keeps the order of sync events (a file, then its delete or ACL
change) in one FIFO queue and retries while the graph is busy (429) or
unreachable. The document ACL is computed when the file is SENT, not when it
was queued, so a later ACL change is never overtaken by an older copy.
Queued bytes stay in memory only; a full queue drops the file and counts it
(re-sync the drive after enlarging ADAPTER_GRAPH_QUEUE_MB).

Disabled unless GRAPH_URL is set: then every graph endpoint answers 503 and
nothing is forwarded.
"""
import base64
import collections
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# Same list as rag_service/extract.py SUPPORTED_EXTS: what the extractor can read.
GRAPH_EXTS = frozenset((
    ".txt", ".md", ".markdown", ".text", ".csv", ".tsv", ".json", ".log", ".rst", ".yaml",
    ".yml", ".ini", ".xml", ".pdf", ".docx", ".pptx", ".ppt", ".xls", ".html", ".htm", ".rtf",
    ".xlsx", ".xlsm"))
QUERIES = frozenset(("find", "mentioned", "entity", "path", "common", "copresence",
                     "shared", "timeline", "neighborhood", "communities", "ranking"))
RECENT = 200


class GraphError(Exception):
    def __init__(self, status, body):
        super().__init__("graph %s: %s" % (status, body.get("error") if isinstance(body, dict)
                                           else body))
        self.status = status
        self.body = body if isinstance(body, dict) else {"ok": False, "error": str(body)}


class GraphClient:
    def __init__(self, url, api_key="", timeout=120):
        self.url = (url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("GRAPH_URL", ""), os.environ.get("GRAPH_API_KEY", ""),
                   float(os.environ.get("ADAPTER_GRAPH_TIMEOUT", "120")))

    @property
    def enabled(self):
        return bool(self.url)

    def post(self, path, obj, timeout=None):
        if not self.enabled:
            raise GraphError(503, {"ok": False, "error": "relationship analysis is not enabled"})
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        req = urllib.request.Request(self.url + path, data=json.dumps(obj).encode("utf-8"),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                body = {"ok": False, "error": "graph http %s" % e.code}
            raise GraphError(e.code, body)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GraphError(503, {"ok": False, "error": "graph unreachable: %s"
                                   % type(e).__name__})


def _under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


class Forwarder:
    """Ordered, retried delivery of synced files and their deletes / ACL changes."""

    def __init__(self, client, acl_of, max_bytes, max_file_bytes, retry_seconds=10.0,
                 autostart=True):
        self.client = client
        self.acl_of = acl_of  # path -> flattened ACL, evaluated at send time
        self.max_bytes = max_bytes
        self.max_file_bytes = max_file_bytes
        self.retry_seconds = retry_seconds
        self.cond = threading.Condition()
        self.queue = collections.deque()
        self.queued_bytes = 0
        self.counts = collections.Counter()
        self.dropped = collections.deque(maxlen=RECENT)
        self.last_error = None
        self._thread = None
        if autostart:
            self.start()

    @classmethod
    def from_env(cls, client, acl_of):
        mb = int(os.environ.get("ADAPTER_GRAPH_QUEUE_MB", "256") or 0)
        file_mb = int(os.environ.get("ADAPTER_GRAPH_MAX_FILE_MB", "32") or 0)
        return cls(client, acl_of, max(1, mb) * 1024 * 1024, max(1, file_mb) * 1024 * 1024)

    def start(self):
        if self._thread is None and self.client.enabled:
            self._thread = threading.Thread(target=self._loop, name="graph-forward", daemon=True)
            self._thread.start()

    # -- producers (sync handlers) ---------------------------------------------

    def file(self, path, corpus_id, doc_id, raw):
        """Queue a committed file. False when it is not forwarded (and why is counted)."""
        if not self.client.enabled:
            return False
        ext = os.path.splitext(path)[1].lower()
        if ext not in GRAPH_EXTS:
            self.counts["skipped_type"] += 1
            return False
        if len(raw) > self.max_file_bytes:
            self._drop(path, "file larger than ADAPTER_GRAPH_MAX_FILE_MB")
            return False
        with self.cond:
            self._remove(lambda item: item["kind"] == "file" and item["path"] == path)
            if self.queued_bytes + len(raw) > self.max_bytes:
                self._drop(path, "forward queue full")
                return False
            self.queue.append({"kind": "file", "path": path, "corpus_id": corpus_id,
                               "doc_id": doc_id, "raw": raw})
            self.queued_bytes += len(raw)
            self.cond.notify()
        return True

    def drop_tree(self, path, corpus_id, keep_exact):
        if not self.client.enabled:
            return
        with self.cond:
            # Files still waiting below a deleted path never need to be sent.
            self._remove(lambda item: item["kind"] == "file" and _under(item["path"], path)
                         and not (keep_exact and item["path"] == path))
            self.queue.append({"kind": "drop", "path": path, "corpus_id": corpus_id,
                               "keep_exact": bool(keep_exact)})
            self.cond.notify()

    def acl(self, corpus_id, entries):
        if not self.client.enabled or not entries:
            return
        with self.cond:
            self.queue.append({"kind": "acl", "corpus_id": corpus_id, "entries": list(entries)})
            self.cond.notify()

    def _remove(self, match):
        for item in [i for i in self.queue if match(i)]:
            self.queue.remove(item)
            self.queued_bytes -= len(item.get("raw") or b"")

    def _drop(self, path, reason):
        self.counts["dropped"] += 1
        self.dropped.append({"path": path, "reason": reason, "ts": time.time()})
        sys.stderr.write("[adapter] graph: not forwarded (%s)\n" % reason)

    # -- delivery ----------------------------------------------------------------

    def _request(self, item):
        if item["kind"] == "file":
            return "/ingest/document", {
                "doc_id": item["doc_id"], "corpus_id": item["corpus_id"],
                "source_path": item["path"], "title": item["path"].rsplit("/", 1)[-1],
                "acl": self.acl_of(item["path"]),
                "content_b64": base64.b64encode(item["raw"]).decode("ascii")}
        if item["kind"] == "drop":
            return "/graph/document/drop_tree", {
                "corpus_id": item["corpus_id"], "path": item["path"],
                "keep_exact": item["keep_exact"]}
        return "/graph/acl/update", {"corpus_id": item["corpus_id"], "entries": item["entries"]}

    def deliver_one(self):
        """Send the head of the queue. True when it left the queue."""
        with self.cond:
            if not self.queue:
                return False
            item = self.queue[0]
        path, body = self._request(item)
        try:
            self.client.post(path, body)
        except GraphError as e:
            self.last_error = {"status": e.status, "error": e.body.get("error"),
                               "ts": time.time()}
            if e.status in (429, 502, 503, 504):
                return False  # busy or away: retry the same item later, order kept
            self.counts["failed"] += 1
            sys.stderr.write("[adapter] graph: %s rejected (%s)\n" % (item["kind"], e))
        else:
            self.counts["sent_" + item["kind"]] += 1
        with self.cond:
            if self.queue and self.queue[0] is item:
                self.queue.popleft()
                self.queued_bytes -= len(item.get("raw") or b"")
        return True

    def _loop(self):
        while True:
            with self.cond:
                while not self.queue:
                    self.cond.wait()
            if not self.deliver_one():
                time.sleep(self.retry_seconds)

    def stats(self):
        with self.cond:
            return {"enabled": self.client.enabled, "pending": len(self.queue),
                    "pending_bytes": self.queued_bytes, "counts": dict(self.counts),
                    "last_error": self.last_error}
