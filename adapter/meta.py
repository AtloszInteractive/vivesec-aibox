"""Metadata mirror for the adapter.

The ViVeSec v2 sync protocol needs `/index/get` and `/index/get/children` so the
ViVeSecBox can diff its filesystem against what the AIBox already has (by mtime +
size). The RAG contract intentionally has NO listing endpoint (it is a pure
index/retrieve service). So the adapter keeps its own lightweight mirror of the
document metadata it has pushed, and answers the diff-sync reads from here.

This is the right separation: the ViVeSec-v2-specific bookkeeping lives in the
adapter, the RAG service stays contract-pure and drop-in swappable.

Stores only {path, file, mtime, size} — never content. Segment-aware prefix
logic mirrors the AIBox index so a `beta` drive never matches `beta dev 2`.
"""
import json
import os
import threading

from corpus import norm


def under(path, prefix):
    prefix = norm(prefix)
    return path == prefix or path.startswith(prefix + "/")


class MetaMirror:
    def __init__(self, persist_path=None):
        self._lock = threading.RLock()
        self._docs = {}  # path -> {path, file, mtime, size}
        self.persist_path = persist_path
        if persist_path and os.path.exists(persist_path):
            self._load()

    def _persist(self):
        if not self.persist_path:
            return
        tmp = self.persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"docs": self._docs}, fh)
        os.replace(tmp, self.persist_path)

    def _load(self):
        with open(self.persist_path, "r", encoding="utf-8") as fh:
            self._docs = json.load(fh).get("docs", {})

    # -- read side -----------------------------------------------------------
    def get(self, path):
        with self._lock:
            meta = self._docs.get(norm(path))
            return dict(meta) if meta else None

    def get_children(self, path):
        base = norm(path)
        prefix = base + "/"
        out = []
        with self._lock:
            for p, meta in self._docs.items():
                if p == base or not p.startswith(prefix):
                    continue
                if "/" not in p[len(prefix):]:  # immediate child only
                    out.append(dict(meta))
        return out

    def find(self, prefix, pattern, files_only=True, limit=50):
        """Filename lookup under `prefix` (the #search files quick action):
        case-insensitive substring match on the basename; empty pattern lists
        everything under the prefix. Sorted by path, capped at `limit`."""
        base = norm(prefix)
        pat = (pattern or "").strip().lower()
        out = []
        with self._lock:
            for p, meta in sorted(self._docs.items()):
                if not under(p, base) or p == base:
                    continue
                if files_only and not meta.get("file"):
                    continue
                name = p.rsplit("/", 1)[-1].lower()
                if pat and pat not in name:
                    continue
                out.append(dict(meta))
                if len(out) >= limit:
                    break
        return out

    def stats(self):
        with self._lock:
            files = sum(1 for m in self._docs.values() if m.get("file"))
            dirs = sum(1 for m in self._docs.values() if not m.get("file"))
            return {"documents": len(self._docs), "files": files, "directories": dirs}

    # -- write side ----------------------------------------------------------
    def upsert(self, path, file, mtime, size):
        path = norm(path)
        with self._lock:
            self._docs[path] = {"path": path, "file": bool(file),
                                "mtime": mtime, "size": size}
            self._persist()

    def drop_tree(self, path, keep_exact=False):
        base = norm(path)
        removed = 0
        with self._lock:
            victims = []
            for p in self._docs:
                if not under(p, base):
                    continue
                if keep_exact and p == base:
                    continue
                victims.append(p)
            for p in victims:
                del self._docs[p]
                removed += 1
            self._persist()
        return removed

    def clear(self):
        """Drop every mirrored document (factory reset). Returns the count."""
        with self._lock:
            removed = len(self._docs)
            self._docs = {}
            self._persist()
        return removed
