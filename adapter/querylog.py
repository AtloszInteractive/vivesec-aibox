"""Query log: who asked what, when, for which purpose, with what result
(relationship analysis concept 5.3).

Append-only JSONL, one file per day under ADAPTER_QUERY_LOG_DIR. Every record
carries the hash of the record before it (`prev`) and its own (`hash`), so a
removed, inserted or edited line breaks the chain (verify()). This is the
tamper-evidence groundwork; signing and external anchoring belong to E06.

The question itself is kept only as a SHA-256 unless ADAPTER_QUERY_LOG_TEXT=on:
a supervisor sees that and why someone asked, and which documents answered,
without the log disclosing the content (5.3).

  ADAPTER_QUERY_LOG_DIR    default /data/querylog; empty disables the log (then
                           purpose-bound queries are refused, see service.py)
  ADAPTER_QUERY_LOG_TEXT   off (default) | on
"""
import datetime
import hashlib
import json
import os
import threading

MAX_SOURCES = 50


def _canonical(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def question_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class QueryLog:
    def __init__(self, root, keep_text=False):
        self.root = root or ""
        self.keep_text = keep_text
        self._lock = threading.Lock()
        self._prev = {}  # file name -> hash of its last record

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(env.get("ADAPTER_QUERY_LOG_DIR", "/data/querylog"),
                   (env.get("ADAPTER_QUERY_LOG_TEXT", "off") or "off").strip().lower() == "on")

    @property
    def enabled(self):
        return bool(self.root)

    def available(self):
        """Can a record be written now?"""
        if not self.enabled:
            return False
        try:
            os.makedirs(self.root, exist_ok=True)
            return os.access(self.root, os.W_OK)
        except OSError:
            return False

    def _last_hash(self, name, path):
        if name in self._prev:
            return self._prev[name]
        last = "start:" + name
        try:
            with open(path, "rb") as handle:
                for line in handle:
                    if line.strip():
                        last = json.loads(line)["hash"]
        except FileNotFoundError:
            pass
        return last

    def record(self, entry, now=None):
        """Append one record; returns it. Raises OSError when it cannot be written."""
        if not self.enabled:
            raise OSError("query log disabled (ADAPTER_QUERY_LOG_DIR empty)")
        now = now or datetime.datetime.now(datetime.timezone.utc)
        name = "queries-%s.jsonl" % now.strftime("%Y%m%d")
        path = os.path.join(self.root, name)
        record = dict(entry)
        record["ts"] = now.isoformat(timespec="seconds")
        question = record.pop("question", None)
        record["question_sha256"] = question_hash(question)
        if self.keep_text and question:
            record["question"] = question
        with self._lock:
            os.makedirs(self.root, exist_ok=True)
            record["prev"] = self._last_hash(name, path)
            record["hash"] = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(_canonical(record) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._prev[name] = record["hash"]
        return record


def verify(path):
    """(ok, records read, line number of the first broken record or None)."""
    name = os.path.basename(path)
    prev = "start:" + name
    count = 0
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                return False, count, number
            stored = record.pop("hash", None)
            if record.get("prev") != prev or stored != hashlib.sha256(
                    _canonical(record).encode("utf-8")).hexdigest():
                return False, count, number
            prev = stored
            count += 1
    return True, count, None


def sources_of(contexts_or_paths):
    """Distinct document paths, at most MAX_SOURCES, in first-seen order."""
    out = []
    for item in contexts_or_paths or []:
        path = item.get("path") if isinstance(item, dict) else item
        if path and path not in out:
            out.append(path)
        if len(out) >= MAX_SOURCES:
            break
    return out
