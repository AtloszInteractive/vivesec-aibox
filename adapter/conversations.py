"""Persistent, multi-thread conversation store for the adapter (F1).

A conversation ("thread") belongs to one (VVS-User, history scope) pair -- the
scope is the request's session scope already namespaced by chat profile
(chat_policy.history_scope), so the ACL isolation is exactly the pre-thread
one: a thread created under one scope or profile is invisible from any other.

Two kinds of thread live side by side:

* the ``default`` thread -- the legacy per-scope session file
  ``<spill_dir>/<scope-hash>.json``.  A request without ``conversation_id``
  reads and writes this file, so callers that never heard of threads behave
  exactly as before;
* named threads -- ``<conversations_dir>/<scope-hash>/<id>.json`` plus an
  ``index.json`` per scope holding the list-view fields (title, timestamps,
  turn count), so listing never has to read the threads themselves.

Storage keeps up to ``max_stored_turns`` exchanges per thread; ``history()``
still hands the model only the last ``max_turns`` exchanges (the pre-thread
window), so the prompt a caller gets is unchanged until the token budget work
(F4) decides otherwise.

Retention: threads untouched for ``retention_days`` are removed by
``retire()``; above ``max_per_scope`` threads the least recently updated one
goes.  The default thread is exempt from both (it is the compatibility path).

Pure stdlib, thread-safe, atomic writes (tmp + os.replace, the jobstore
pattern).  Memory is a write-through cache evicted by ``sweep()`` after
``idle_seconds`` of inactivity; the disk copy is authoritative.
"""
import copy
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid

DEFAULT_ID = "default"
TITLE_MAX_CHARS = 120
_FALLBACK_TITLE_CHARS = 60
_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


def scope_key(user, scope):
    """Stable, filesystem-safe identifier for a (user, scope) pair -- the same
    formula as session.session_key, so the legacy session file is found."""
    raw = (user or "") + "\x00" + (scope or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def new_id():
    return uuid.uuid4().hex


def valid_id(conversation_id):
    return conversation_id == DEFAULT_ID or bool(_ID_RE.match(conversation_id or ""))


def fallback_title(question):
    """Deterministic title when no model-made one exists yet: the first line of
    the first question, cut at a word boundary."""
    text = " ".join((question or "").split())
    if len(text) <= _FALLBACK_TITLE_CHARS:
        return text
    cut = text[:_FALLBACK_TITLE_CHARS]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"


class ConversationStore:
    def __init__(self, spill_dir=None, idle_seconds=900, max_turns=12,
                 max_stored_turns=200, retention_days=90, max_per_scope=50,
                 conversations_dir=None, clock=time.time):
        self.spill_dir = spill_dir or None
        self.conversations_dir = (conversations_dir
                                  or (os.path.join(self.spill_dir, "conversations")
                                      if self.spill_dir else None))
        self.idle_seconds = int(idle_seconds)
        self.max_turns = max(1, int(max_turns))
        self.max_stored_turns = max(self.max_turns, int(max_stored_turns))
        self.retention_seconds = max(0.0, float(retention_days)) * 86400
        self.max_per_scope = max(1, int(max_per_scope))
        self._clock = clock
        self._mem = {}  # (key, conversation_id) -> record (+ "_ts" idle stamp)
        self._lock = threading.RLock()
        for directory in (self.spill_dir, self.conversations_dir):
            if directory:
                try:
                    os.makedirs(directory, exist_ok=True)
                except OSError:
                    # LUKS-backed /data may not be mounted until the box unlocks.
                    pass

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        spill_dir = (env.get("ADAPTER_SESSION_DIR") or "").strip() or None
        return cls(
            spill_dir=spill_dir,
            idle_seconds=int(env.get("ADAPTER_SESSION_IDLE", "900") or 900),
            max_turns=int(env.get("ADAPTER_SESSION_TURNS", "12") or 12),
            max_stored_turns=int(env.get("ADAPTER_CONVERSATION_MAX_TURNS", "200") or 200),
            retention_days=float(env.get("ADAPTER_CONVERSATION_RETENTION_DAYS", "90") or 90),
            max_per_scope=int(env.get("ADAPTER_CONVERSATIONS_PER_USER", "50") or 50),
            conversations_dir=(env.get("ADAPTER_CONVERSATIONS_DIR") or "").strip() or None,
        )

    # -- paths -----------------------------------------------------------------
    def _scope_dir(self, key):
        return os.path.join(self.conversations_dir, key) if self.conversations_dir else None

    def _path(self, key, conversation_id):
        if conversation_id == DEFAULT_ID:
            return os.path.join(self.spill_dir, key + ".json") if self.spill_dir else None
        directory = self._scope_dir(key)
        return os.path.join(directory, conversation_id + ".json") if directory else None

    def _index_path(self, key):
        directory = self._scope_dir(key)
        return os.path.join(directory, "index.json") if directory else None

    @staticmethod
    def _read_json(path):
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write_json(path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)

    # -- records -------------------------------------------------------------------
    def _new_record(self, user, scope, conversation_id, title=""):
        now = self._clock()
        return {"id": conversation_id, "user": user or "", "drive": scope or "",
                "title": title or "", "title_source": "user" if title else "",
                "created_ts": now, "updated_ts": now, "turns": [], "_ts": now}

    def _load(self, key, user, scope, conversation_id):
        """Load one thread from disk into memory (caller holds the lock). A legacy
        session file (no id/title/timestamps) is read as the default thread."""
        path = self._path(key, conversation_id)
        data = self._read_json(path)
        if data is None:
            return None
        if data.get("user", user or "") != (user or "") or data.get("drive", scope or "") != (scope or ""):
            return None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = self._clock()
        record = {"id": conversation_id, "user": data.get("user", user or ""),
                  "drive": data.get("drive", scope or ""),
                  "title": data.get("title") or "",
                  "title_source": data.get("title_source") or "",
                  "created_ts": float(data.get("created_ts") or mtime),
                  "updated_ts": float(data.get("updated_ts") or mtime),
                  "turns": list(data.get("turns", [])), "_ts": self._clock()}
        self._mem[(key, conversation_id)] = record
        return record

    def _get(self, user, scope, conversation_id, create=False):
        conversation_id = conversation_id or DEFAULT_ID
        if not valid_id(conversation_id):
            return None, None
        key = scope_key(user, scope)
        record = self._mem.get((key, conversation_id))
        if record is None:
            record = self._load(key, user, scope, conversation_id)
        if record is None and create and conversation_id == DEFAULT_ID:
            record = self._new_record(user, scope, conversation_id)
            self._mem[(key, conversation_id)] = record
        return key, record

    def _persist(self, key, record):
        path = self._path(key, record["id"])
        if not path:
            return
        payload = {field: record[field] for field in
                   ("id", "user", "drive", "title", "title_source",
                    "created_ts", "updated_ts", "turns")}
        self._write_json(path, payload)
        self._index_put(key, record)

    # -- index (list view without reading the threads) -----------------------------
    @staticmethod
    def _summary(record):
        return {"id": record["id"], "title": record.get("title") or "",
                "title_source": record.get("title_source") or "",
                "created_ts": record.get("created_ts"), "updated_ts": record.get("updated_ts"),
                "turn_count": len(record.get("turns") or []) // 2}

    def _index_read(self, key):
        return self._read_json(self._index_path(key)) or {}

    def _index_write(self, key, index):
        path = self._index_path(key)
        if path:
            self._write_json(path, index)

    def _index_put(self, key, record):
        if not self.conversations_dir:
            return
        index = self._index_read(key)
        index[record["id"]] = self._summary(record)
        self._index_write(key, index)

    def _index_drop(self, key, conversation_id):
        if not self.conversations_dir:
            return
        index = self._index_read(key)
        if conversation_id in index:
            del index[conversation_id]
            self._index_write(key, index)

    def _enforce_cap(self, key):
        """Above max_per_scope named threads the least recently updated goes."""
        index = self._index_read(key)
        named = [entry for cid, entry in index.items() if cid != DEFAULT_ID]
        if len(named) <= self.max_per_scope:
            return 0
        named.sort(key=lambda entry: entry.get("updated_ts") or 0)
        removed = 0
        for entry in named[:len(named) - self.max_per_scope]:
            if self._remove(key, entry["id"]):
                removed += 1
        return removed

    def _remove(self, key, conversation_id):
        self._mem.pop((key, conversation_id), None)
        path = self._path(key, conversation_id)
        removed = False
        if path:
            for candidate in (path, path + ".tmp"):
                try:
                    os.remove(candidate)
                    removed = removed or candidate == path
                except OSError:
                    pass
        self._index_drop(key, conversation_id)
        return removed

    # -- public API: history for the model -----------------------------------------
    def history(self, user, scope, conversation_id=None):
        """Recent turns (oldest->newest) of one thread for the LLM -- the last
        max_turns exchanges, as {"role","content"[,"sources","ts"]} dicts."""
        with self._lock:
            _, record = self._get(user, scope, conversation_id)
            if not record:
                return []
            record["_ts"] = self._clock()
            return copy.deepcopy(record["turns"][-self.max_turns * 2:])

    def record(self, user, scope, question, answer, sources=None, conversation_id=None):
        """Append one exchange to a thread, trim the stored window, persist.
        A named thread must exist (create() first); the default thread is made
        on demand. Returns the number of exchanges now stored, or None when the
        thread is unknown."""
        with self._lock:
            key, record = self._get(user, scope, conversation_id, create=True)
            if record is None:
                return None
            now = self._clock()
            record["turns"].append({"role": "user", "content": question or "", "ts": now})
            turn = {"role": "assistant", "content": answer or "", "ts": now}
            if sources:
                turn["sources"] = copy.deepcopy(sources[:8])
            record["turns"].append(turn)
            cap = self.max_stored_turns * 2
            if len(record["turns"]) > cap:
                record["turns"] = record["turns"][-cap:]
            if not record.get("title"):
                record["title"] = fallback_title(question)
                record["title_source"] = "fallback"
            record["updated_ts"] = now
            record["_ts"] = now
            self._persist(key, record)
            return len(record["turns"]) // 2

    # -- public API: thread management ------------------------------------------------
    def exists(self, user, scope, conversation_id):
        if not conversation_id or conversation_id == DEFAULT_ID:
            return True
        with self._lock:
            _, record = self._get(user, scope, conversation_id)
            return record is not None

    def list(self, user, scope):
        """Thread summaries for the list view, most recently updated first. The
        default thread is listed once it has content."""
        with self._lock:
            key = scope_key(user, scope)
            index = dict(self._index_read(key))
            # In-memory records may be newer than the index; the default thread
            # of a pre-thread deployment is on disk but not indexed yet.
            for (mem_key, cid), record in self._mem.items():
                if mem_key == key:
                    index[cid] = self._summary(record)
            if DEFAULT_ID not in index:
                _, default = self._get(user, scope, DEFAULT_ID)
                if default is not None:
                    index[DEFAULT_ID] = self._summary(default)
            entries = [entry for cid, entry in index.items()
                       if cid != DEFAULT_ID or entry.get("turn_count")]
            entries.sort(key=lambda entry: entry.get("updated_ts") or 0, reverse=True)
            return entries

    def create(self, user, scope, title=None):
        with self._lock:
            key = scope_key(user, scope)
            record = self._new_record(user, scope, new_id(), (title or "").strip()[:TITLE_MAX_CHARS])
            self._mem[(key, record["id"])] = record
            self._persist(key, record)
            self._enforce_cap(key)
            return self._public(record)

    def get(self, user, scope, conversation_id):
        with self._lock:
            _, record = self._get(user, scope, conversation_id)
            if record is None:
                return None
            record["_ts"] = self._clock()
            return self._public(record)

    def rename(self, user, scope, conversation_id, title):
        return self.set_title(user, scope, conversation_id, title, source="user")

    def set_title(self, user, scope, conversation_id, title, source="auto"):
        """Set the title. An automatic title never overrides one the user typed,
        and only replaces the deterministic fallback."""
        title = " ".join((title or "").split())[:TITLE_MAX_CHARS]
        if not title:
            return None
        with self._lock:
            key, record = self._get(user, scope, conversation_id, create=True)
            if record is None:
                return None
            current = record.get("title_source") or ""
            if source != "user" and current not in ("", "fallback"):
                return self._public(record)
            record["title"] = title
            record["title_source"] = source
            record["_ts"] = self._clock()
            self._persist(key, record)
            return self._public(record)

    def delete(self, user, scope, conversation_id):
        """Physically remove one thread (the default thread's file included).
        Returns True when something was removed, False for an unknown id."""
        with self._lock:
            key, record = self._get(user, scope, conversation_id)
            if record is None:
                return False
            self._remove(key, record["id"])
            return True

    @staticmethod
    def _public(record):
        return {"id": record["id"], "title": record.get("title") or "",
                "title_source": record.get("title_source") or "",
                "created_ts": record.get("created_ts"), "updated_ts": record.get("updated_ts"),
                "turn_count": len(record.get("turns") or []) // 2,
                "turns": copy.deepcopy(record.get("turns") or [])}

    # -- housekeeping -----------------------------------------------------------------
    def sweep(self):
        """Evict threads idle longer than idle_seconds from memory; their disk
        copy remains so a returning user reloads them transparently."""
        if not self.spill_dir:
            return 0
        cutoff = self._clock() - self.idle_seconds
        evicted = 0
        with self._lock:
            for item in [k for k, r in self._mem.items() if r["_ts"] < cutoff]:
                self._persist(item[0], self._mem[item])
                del self._mem[item]
                evicted += 1
        return evicted

    def retire(self):
        """Apply the retention window: named threads not updated within
        retention_days are removed, every scope's count cap is re-applied."""
        if not self.conversations_dir or not self.retention_seconds:
            return 0
        cutoff = self._clock() - self.retention_seconds
        removed = 0
        with self._lock:
            try:
                keys = [name for name in os.listdir(self.conversations_dir)
                        if os.path.isdir(os.path.join(self.conversations_dir, name))]
            except OSError:
                return 0
            for key in keys:
                for cid, entry in list(self._index_read(key).items()):
                    if cid == DEFAULT_ID:
                        continue
                    if (entry.get("updated_ts") or 0) < cutoff and self._remove(key, cid):
                        removed += 1
                removed += self._enforce_cap(key)
        return removed

    def flush_memory(self):
        """Drop every thread from memory (persisting first). Used on a
        presence-lock; disk copies survive for a clean reload after unlock."""
        with self._lock:
            for (key, _), record in list(self._mem.items()):
                self._persist(key, record)
            dropped = len(self._mem)
            self._mem.clear()
        return dropped

    def purge(self):
        """Delete ALL conversation state -- memory, the legacy session files and
        every thread (factory reset). Returns files removed."""
        with self._lock:
            self._mem.clear()
            removed = 0
            if self.spill_dir and os.path.isdir(self.spill_dir):
                for name in os.listdir(self.spill_dir):
                    if name.endswith(".json") or name.endswith(".json.tmp"):
                        try:
                            os.remove(os.path.join(self.spill_dir, name))
                            removed += 1
                        except OSError:
                            pass
            if self.conversations_dir and os.path.isdir(self.conversations_dir):
                for name in os.listdir(self.conversations_dir):
                    path = os.path.join(self.conversations_dir, name)
                    if not os.path.isdir(path):
                        continue
                    removed += sum(1 for entry in os.listdir(path)
                                   if entry.endswith(".json") and entry != "index.json")
                    shutil.rmtree(path, ignore_errors=True)
            return removed

    def stats(self):
        with self._lock:
            return {"active": len(self._mem), "spill_dir": self.spill_dir,
                    "conversations_dir": self.conversations_dir,
                    "idle_seconds": self.idle_seconds, "max_turns": self.max_turns,
                    "max_stored_turns": self.max_stored_turns,
                    "retention_days": self.retention_seconds / 86400,
                    "max_per_scope": self.max_per_scope}
