"""Query log: who asked what, when, for which purpose, with what result
(relationship analysis concept 5.3).

Append-only JSONL, one file per day under ADAPTER_QUERY_LOG_DIR. Every record
carries the hash of the record before it (`prev`) and its own (`hash`), so a
removed, inserted or edited line breaks the chain (verify()). This is the
tamper-evidence groundwork; signing and external anchoring belong to E06.

The question itself is kept only as a SHA-256 unless ADAPTER_QUERY_LOG_TEXT=on:
a supervisor sees that and why someone asked, and which documents answered,
without the log disclosing the content (5.3).

Day files older than ADAPTER_QUERY_LOG_RETENTION_DAYS are removed (BV: five
years); each day file is its own chain, so the rest still verifies. read()
serves the log view: an analyst their own records, a leader everyone's (the
caller decides who may see what, service.py).

  ADAPTER_QUERY_LOG_DIR             default /data/querylog; empty disables the log
                                    (then purpose-bound queries are refused, see
                                    service.py)
  ADAPTER_QUERY_LOG_TEXT            off (default) | on
  ADAPTER_QUERY_LOG_RETENTION_DAYS  0 (default): keep every day file; an invalid
                                    value keeps them too (removal is irreversible)
"""
import datetime
import hashlib
import json
import os
import re
import sys
import threading

MAX_SOURCES = 50
# One view reads at most this many days of files.
MAX_VIEW_DAYS = 366
MAX_VIEW_LIMIT = 200
_FILE = re.compile(r"^queries-(\d{8})\.jsonl$")


def _canonical(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def question_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class QueryLog:
    def __init__(self, root, keep_text=False, retention_days=0):
        self.root = root or ""
        self.keep_text = keep_text
        self.retention_days = max(0, int(retention_days or 0))
        self._lock = threading.Lock()
        self._prev = {}  # file name -> hash of its last record
        self._purged = None  # the UTC day the old files were last removed

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        raw = (env.get("ADAPTER_QUERY_LOG_RETENTION_DAYS", "") or "0").strip()
        try:
            days = int(raw)
            if days < 0:
                raise ValueError(raw)
        except ValueError:
            sys.stderr.write("[adapter] invalid ADAPTER_QUERY_LOG_RETENTION_DAYS %r -> 0 "
                             "(every day file is kept)\n" % raw)
            days = 0
        return cls(env.get("ADAPTER_QUERY_LOG_DIR", "/data/querylog"),
                   (env.get("ADAPTER_QUERY_LOG_TEXT", "off") or "off").strip().lower() == "on",
                   days)

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
            if self._purged != name:
                self._purged = name
                try:
                    self._purge(now)
                except OSError as error:
                    sys.stderr.write("[adapter] query log retention: %s\n" % error)
        return record

    def _days(self):
        """[(YYYYMMDD, path)] of the day files, oldest first."""
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        out = []
        for name in names:
            match = _FILE.match(name)
            if match:
                out.append((match.group(1), os.path.join(self.root, name)))
        return sorted(out)

    def _purge(self, now):
        removed = []
        if not self.retention_days:
            return removed
        cutoff = (now.date() - datetime.timedelta(days=self.retention_days)).strftime("%Y%m%d")
        for day, path in self._days():
            if day < cutoff:
                os.remove(path)
                self._prev.pop(os.path.basename(path), None)
                removed.append(os.path.basename(path))
        return removed

    def purge(self, now=None):
        """Removes the day files older than the retention; the names removed."""
        if not self.enabled:
            return []
        with self._lock:
            return self._purge(now or datetime.datetime.now(datetime.timezone.utc))

    def read(self, since, until, user=None, case_ref=None, offset=0, limit=50, before=None):
        """The records of the days since..until (datetime.date, both included),
        newest first: {"records", "total", "as_of"}. `user` keeps that user's
        records (case-insensitive), `case_ref` those whose case reference
        contains it. `as_of` ("YYYYMMDD:N": the newest day file and its record
        count at this read) passed back as `before` leaves out every record
        written since, so later pages of one view stay put. Each record says
        whether its day file's chain is intact; a damaged file is shown as such
        and never stops the others."""
        first, last = since.strftime("%Y%m%d"), until.strftime("%Y%m%d")
        user = (user or "").casefold() or None
        case_ref = (case_ref or "").casefold() or None
        limit = max(1, min(int(limit), MAX_VIEW_LIMIT))
        offset = max(0, int(offset))
        cut_day, cut_lines = None, None
        if before and re.fullmatch(r"\d{8}:\d+", before):
            cut_day, cut_lines = before.split(":")[0], int(before.split(":")[1])
        days = self._days()
        as_of = before if cut_day else "00000000:0"
        records, total = [], 0
        for day, path in reversed(days):
            lines = _lines(path)
            if not cut_day and day == days[-1][0]:
                as_of = "%s:%d" % (day, len(lines))
            if cut_day and day > cut_day:
                continue
            if not first <= day <= last:
                continue
            if cut_day and day == cut_day:
                lines = lines[:cut_lines]
            intact = _intact(path)
            for line in reversed(lines):
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                if user and str(record.get("user") or "").casefold() != user:
                    continue
                if case_ref and case_ref not in str(record.get("case_ref") or "").casefold():
                    continue
                total += 1
                if offset < total <= offset + limit:
                    record.pop("prev", None)
                    record["intact"] = intact
                    records.append(record)
        return {"records": records, "total": total, "as_of": as_of}


def _lines(path):
    """The non-empty lines of a day file ([] when unreadable); undecodable bytes
    are replaced, so a damaged file still lists its readable records."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return [line for line in handle if line.strip()]
    except OSError:
        return []


def _intact(path):
    try:
        return verify(path)[0]
    except (OSError, ValueError):
        return False


def view_range(since, until, today=None):
    """(since, until) dates of a log view from "YYYY-MM-DD" strings: the last
    30 days by default, at most MAX_VIEW_DAYS. ValueError when unusable."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()

    def day(value, default):
        if value in (None, ""):
            return default
        return datetime.date.fromisoformat(str(value))
    until = day(until, today)
    since = day(since, until - datetime.timedelta(days=29))
    if since > until:
        raise ValueError("'from' is after 'to'")
    if (until - since).days >= MAX_VIEW_DAYS:
        raise ValueError("at most %d days at a time" % MAX_VIEW_DAYS)
    return since, until


def verify(path):
    """(ok, records read, line number of the first broken record or None)."""
    name = os.path.basename(path)
    prev = "start:" + name
    count = 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                return False, count, number
            if not isinstance(record, dict):
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
