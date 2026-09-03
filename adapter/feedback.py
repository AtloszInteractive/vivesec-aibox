"""User feedback on generated answers (good / not good).

Purpose: when a customer (e.g. ViVeTech) tests the box on their own documents,
their ratings — joined with the full answer trace — become the seed of a
human-validated gold set for THAT corpus, plus a confidence-calibration sample.

Design:
  * The client only sends the rating (+ optional reason/comment) and the
    answer's audit id. The trace (question, retrieved hits, citations,
    confidence, versions) is joined in SERVER-side from a bounded in-memory
    registry filled at answer time, so a rating cannot rewrite what the box
    actually said. If the trace has already been evicted (restart/overflow),
    the rating is still stored together with the client's echo.
  * Storage is append-only JSONL, one file per day under the feedback root
    (/data on the box: the data never leaves the appliance unless the
    customer exports it). Re-rating appends; analysis takes the last record
    per audit_id.

Pure stdlib, thread-safe.
"""
import collections
import json
import os
import threading
import time

RATINGS = ("up", "down")
# Failure taxonomy the UI offers on a down-vote; maps onto the eval categories
# (retrieval miss / hallucination / refusal correctness / coverage / format).
REASONS = ("wrong-source", "ungrounded", "unnecessary-refusal",
           "incomplete", "format", "other")


class TraceRegistry:
    """Bounded audit_id -> answer-trace map (newest kept). get() does not
    evict: the same answer may be re-rated."""

    def __init__(self, cap=200):
        self.cap = max(1, int(cap))
        self._map = collections.OrderedDict()
        self._lock = threading.Lock()

    def put(self, audit_id, trace):
        if not audit_id:
            return
        with self._lock:
            self._map.pop(audit_id, None)
            self._map[audit_id] = trace
            while len(self._map) > self.cap:
                self._map.popitem(last=False)

    def get(self, audit_id):
        with self._lock:
            return self._map.get(audit_id)


class FeedbackStore:
    """Append-only JSONL store: <root>/feedback-YYYYMMDD.jsonl."""

    def __init__(self, root):
        self.root = (root or "").strip() or None
        self._lock = threading.Lock()
        if self.root:
            try:
                os.makedirs(self.root, exist_ok=True)
            except OSError:
                # e.g. LUKS mode before unlock; record() retries the makedirs.
                pass

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        return cls(env.get("ADAPTER_FEEDBACK_DIR", "/data/feedback"))

    def enabled(self):
        return bool(self.root)

    def record(self, entry):
        """Append one feedback record; returns the file name it went into.
        Raises OSError when the store is unavailable."""
        if not self.root:
            raise OSError("feedback store disabled (ADAPTER_FEEDBACK_DIR empty)")
        name = "feedback-%s.jsonl" % time.strftime("%Y%m%d")
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            os.makedirs(self.root, exist_ok=True)
            with open(os.path.join(self.root, name), "a", encoding="utf-8") as f:
                f.write(line)
        return name
