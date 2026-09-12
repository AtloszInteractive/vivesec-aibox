"""Multi-turn conversation session manager for the adapter.

The ViVeSecBox has no explicit session start/end signal; the AIBox keeps a
user's conversation in memory and spills it to disk after inactivity, reloading
it when the user returns (a requirement confirmed by the ViVeSecBox team).

A session is identified by the (VVS-User, VVS-Drive) pair. Because the ACL is
per-request and drive-scoped (the drive decides what the user may see), the
conversation is drive-scoped too: the same user on a different drive gets a
separate, isolated conversation -- this prevents one drive's context from
leaking into another drive's answers.

Pure stdlib, thread-safe. Disk layout: one JSON file per session under
spill_dir, named by a stable hash of the (user, drive) key, written atomically.
Memory is a write-through cache: every recorded exchange is persisted, and idle
sessions are evicted from memory (their disk copy stays) by a periodic sweep.
"""
import hashlib
import copy
import json
import os
import re
import threading
import time


def session_key(user, drive):
    """Stable, filesystem-safe identifier for a (user, drive) conversation."""
    raw = (user or "") + "\x00" + (drive or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def followup_evidence(question, history, corpus_ids):
    followup = re.search(
        r"\b(?:korábban|előbb|előző|ellentmond\w*|mégis|visszavon\w*|azt mondtad|"
        r"azt írtad|ezt írtad|miért állít\w*|previous|earlier|you said|you wrote|contradict\w*)\b",
        question, re.I)
    short_reference = len(question.split()) <= 14 and re.search(
        r"\b(?:róla|őt|ő|erről|ebben|ezt|ennek|ugyanez|that|it|he|she|they)\b", question, re.I)
    if not followup and not short_reference:
        return []
    evidence = []
    for turn in reversed(history[-6:]):
        if turn.get("role") != "assistant":
            continue
        for source in turn.get("sources", []):
            chunk_id = source.get("chunk_id")
            if source.get("corpus_id") in corpus_ids and chunk_id and chunk_id not in evidence:
                evidence.append(chunk_id)
    return evidence[:8]


class SessionManager:
    """In-memory conversation store with write-through disk spill.

    max_turns counts EXCHANGES (a user question + its assistant answer); the
    flat message list is capped at max_turns * 2 messages.
    """

    def __init__(self, spill_dir=None, idle_seconds=900, max_turns=12):
        self.spill_dir = spill_dir or None
        self.idle_seconds = int(idle_seconds)
        self.max_turns = max(1, int(max_turns))
        self._mem = {}  # key -> {"user","drive","turns":[{role,content}],"ts"}
        self._lock = threading.RLock()
        if self.spill_dir:
            os.makedirs(self.spill_dir, exist_ok=True)

    @classmethod
    def from_env(cls):
        return cls(
            spill_dir=os.environ.get("ADAPTER_SESSION_DIR") or None,
            idle_seconds=int(os.environ.get("ADAPTER_SESSION_IDLE", "900") or 900),
            max_turns=int(os.environ.get("ADAPTER_SESSION_TURNS", "12") or 12),
        )

    # -- disk spill ----------------------------------------------------------
    def _path(self, key):
        return os.path.join(self.spill_dir, key + ".json") if self.spill_dir else None

    def _load(self, key):
        """Load a spilled session into memory (caller holds the lock)."""
        p = self._path(key)
        if not p or not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            return None
        sess = {"user": data.get("user", ""), "drive": data.get("drive", ""),
                "turns": list(data.get("turns", [])), "ts": time.time()}
        self._mem[key] = sess
        return sess

    def _persist(self, key, sess):
        p = self._path(key)
        if not p:
            return
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"user": sess["user"], "drive": sess["drive"],
                       "turns": sess["turns"]}, f, ensure_ascii=False)
        os.replace(tmp, p)

    def _get(self, user, drive, create=False):
        key = session_key(user, drive)
        sess = self._mem.get(key)
        if sess is None:
            sess = self._load(key)
        if sess is None and create:
            sess = {"user": user or "", "drive": drive or "", "turns": [],
                    "ts": time.time()}
            self._mem[key] = sess
        return key, sess

    # -- public API ----------------------------------------------------------
    def history(self, user, drive):
        """Recent conversation turns (oldest->newest) for the LLM, as a list of
        {"role","content"} dicts. Empty for a fresh conversation."""
        with self._lock:
            _, sess = self._get(user, drive)
            if not sess:
                return []
            sess["ts"] = time.time()
            return copy.deepcopy(sess["turns"])

    def record(self, user, drive, question, answer, sources=None):
        """Append the user question + assistant answer, trim to the window, and
        persist. Returns the number of exchanges now stored."""
        with self._lock:
            key, sess = self._get(user, drive, create=True)
            sess["turns"].append({"role": "user", "content": question or ""})
            turn = {"role": "assistant", "content": answer or ""}
            if sources:
                turn["sources"] = copy.deepcopy(sources[:8])
            sess["turns"].append(turn)
            cap = self.max_turns * 2
            if len(sess["turns"]) > cap:
                sess["turns"] = sess["turns"][-cap:]
            sess["ts"] = time.time()
            self._persist(key, sess)
            return len(sess["turns"]) // 2

    def sweep(self):
        """Evict sessions idle longer than idle_seconds from memory; their disk
        copy remains so a returning user reloads it transparently."""
        if not self.spill_dir:
            return 0
        cutoff = time.time() - self.idle_seconds
        evicted = 0
        with self._lock:
            for key in [k for k, s in self._mem.items() if s["ts"] < cutoff]:
                self._persist(key, self._mem[key])
                del self._mem[key]
                evicted += 1
        return evicted

    def flush_memory(self):
        """Drop every conversation from memory (persisting first when a spill dir
        is configured). Used on a presence-lock: the box is assumed stolen, so
        no cleartext-derived conversation should linger in RAM. Disk copies
        survive so a returning user transparently reloads after unlock."""
        with self._lock:
            for key, sess in list(self._mem.items()):
                self._persist(key, sess)
            dropped = len(self._mem)
            self._mem.clear()
        return dropped

    def purge(self):
        """Delete ALL conversation state -- memory AND the disk spill (factory
        reset). Unlike flush_memory, nothing survives. Returns files removed."""
        with self._lock:
            self._mem.clear()
            removed = 0
            if self.spill_dir and os.path.isdir(self.spill_dir):
                for fn in os.listdir(self.spill_dir):
                    if fn.endswith(".json") or fn.endswith(".json.tmp"):
                        try:
                            os.remove(os.path.join(self.spill_dir, fn))
                            removed += 1
                        except OSError:
                            pass
            return removed

    def stats(self):
        with self._lock:
            return {"active": len(self._mem), "spill_dir": self.spill_dir,
                    "idle_seconds": self.idle_seconds, "max_turns": self.max_turns}
