"""Resumable whole-document analysis (E10a): the parts already read, kept on disk.

Reading one part of a long document is a model call of a minute or more. When
an analysis stops before its end (adapter restart, cancel, a failed call), the
same analysis started again takes the parts read so far from here and reads
only the rest.

An entry is found only with the exact passages the caller's own request got
back from the RAG (their hash is part of the key), together with the user, the
task, the language, the part size, the prompt and the model. So access is
decided anew on every run, a changed document starts afresh, and one user never
picks up another's work. Entries expire after ADAPTER_ANALYSIS_RETENTION_DAYS.

  ADAPTER_ANALYSIS_DIR             default: "analysis" beside ADAPTER_JOBS_DIR;
                                   "off" (or no jobs dir) turns resuming off
  ADAPTER_ANALYSIS_RETENTION_DAYS  default 7, as the job store
"""
import hashlib
import json
import os
import threading
import time

VERSION = 1


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def passages_hash(chunks):
    """What was read: every passage with its place in the document."""
    return _digest([[c.get("source_path"), c.get("corpus_id"), c.get("page_number"),
                     c.get("chunk_id"), c.get("text") or ""] for c in chunks])


class Checkpoint:
    """The finished parts of one analysis; part i is a part summary or None
    (read, nothing found). A failed part is never stored, so it is read again."""

    def __init__(self, store, path):
        self._store = store
        self._path = path
        self._parts = {}
        data = store._read(path)
        if data and data.get("version") == VERSION:
            self._parts = data.get("parts") or {}
        self._created = (data or {}).get("created") or time.time()

    def get(self, index):
        """(True, part) when part `index` was read before, else (False, None)."""
        key = str(index)
        return (True, self._parts[key]) if key in self._parts else (False, None)

    def put(self, index, part):
        self._parts[str(index)] = part
        try:
            self._store._write(self._path, {"version": VERSION, "created": self._created,
                                            "updated": time.time(), "parts": self._parts})
        except OSError:
            pass  # resuming is a convenience; the analysis itself goes on


class CheckpointStore:
    def __init__(self, root=None, retention_days=7):
        self.root = root or None
        self.retention_seconds = max(0.0, float(retention_days)) * 86400
        self._lock = threading.Lock()
        self._swept = 0.0

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        root = (env.get("ADAPTER_ANALYSIS_DIR", "") or "").strip()
        if root.lower() == "off":
            root = ""
        elif not root:
            jobs = (env.get("ADAPTER_JOBS_DIR", "") or "").strip().rstrip("/\\")
            # Beside the job store, not inside it: the store reads every file under its root.
            root = os.path.join(os.path.dirname(jobs), "analysis") if jobs else ""
        try:
            days = float(env.get("ADAPTER_ANALYSIS_RETENTION_DAYS", "7") or 7)
        except ValueError:
            days = 7.0
        return cls(root or None, days)

    def enabled(self):
        return bool(self.root)

    def open(self, user, chunks, task, lang, model, settings):
        """The checkpoint of this exact analysis, or None when resuming is off
        or the store cannot be used (the analysis then simply runs in full).
        `settings` holds whatever else shapes the parts (part size, prompt)."""
        if not self.root:
            return None
        key = _digest([VERSION, user or "", model or "", lang or "", task or "",
                       settings or {}, passages_hash(chunks)])
        try:
            os.makedirs(self.root, mode=0o700, exist_ok=True)
            self.sweep()
            return Checkpoint(self, os.path.join(self.root, key + ".json"))
        except OSError:
            return None

    def _read(self, path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _write(self, path, data):
        with self._lock:
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)

    def sweep(self, now=None):
        """Drops entries untouched for longer than the retention (at most hourly)."""
        now = time.time() if now is None else now
        if not self.root or now - self._swept < 3600:
            return 0
        self._swept = now
        removed = 0
        with self._lock:
            try:
                names = os.listdir(self.root)
            except OSError:
                return 0
            for name in names:
                path = os.path.join(self.root, name)
                try:
                    if now - os.path.getmtime(path) > self.retention_seconds:
                        os.remove(path)
                        removed += 1
                except OSError:
                    pass
        return removed
