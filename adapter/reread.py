"""Reading stored documents again for the search (standalone box, E10b).

The search lists the documents of a scope it could not read: a scan whose OCR
was interrupted by a restart or deferred by a long queue, a PDF without a text
layer from before OCR was installed. On a standalone box the files are held
here (drivestore.py), so a store writer can send them through extraction --
OCR included -- again: the stored bytes are indexed exactly like a fresh upload
(service._store_index). One document at a time, in the background, so a
reread never piles up work in front of the questions.

The queue and the outcome of each path live in memory only: a restart forgets
them, and the search list shows what is still unreadable.
"""
import collections
import sys
import threading
import time

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


class Rereader:
    def __init__(self, index, max_queued=2000, keep=2000, autostart=True):
        """index(path) -> result dict; raises when the path cannot be indexed now."""
        self.index = index
        self.max_queued = max_queued
        self.keep = keep
        self.cond = threading.Condition()
        self.queue = collections.deque()
        self.states = collections.OrderedDict()  # path -> {"state", "error", "at"}
        self._thread = None
        self._autostart = autostart

    def submit(self, paths):
        """Queue paths not already waiting; returns (queued, refused: full queue)."""
        queued, refused = [], []
        with self.cond:
            for path in paths:
                state = (self.states.get(path) or {}).get("state")
                if state in (QUEUED, RUNNING):
                    continue
                if len(self.queue) >= self.max_queued:
                    refused.append(path)
                    continue
                self.queue.append(path)
                self._set(path, QUEUED)
                queued.append(path)
            self.cond.notify()
        if queued and self._autostart:
            self.start()
        return queued, refused

    def status(self, path):
        with self.cond:
            entry = self.states.get(path)
            return dict(entry) if entry else None

    def pending(self):
        with self.cond:
            return len(self.queue) + sum(1 for e in self.states.values()
                                         if e["state"] == RUNNING)

    def start(self):
        with self.cond:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="reread", daemon=True)
                self._thread.start()

    def run_once(self):
        """Process one queued path on this thread (tests); False when idle."""
        with self.cond:
            if not self.queue:
                return False
            path = self.queue.popleft()
            self._set(path, RUNNING)
        self._process(path)
        return True

    def wait_idle(self, timeout=10.0):
        deadline = time.time() + timeout
        with self.cond:
            while self.queue or any(e["state"] == RUNNING for e in self.states.values()):
                left = deadline - time.time()
                if left <= 0:
                    return False
                self.cond.wait(left)
        return True

    def _set(self, path, state, error=None):
        self.states.pop(path, None)
        self.states[path] = {"state": state, "error": error, "at": time.time()}
        while len(self.states) > self.keep:
            oldest = next(iter(self.states))
            if self.states[oldest]["state"] in (QUEUED, RUNNING):
                break
            self.states.pop(oldest)

    def _process(self, path):
        try:
            self.index(path)
            state, error = DONE, None
        except Exception as e:  # noqa: BLE001 -- one bad file must not stop the queue
            sys.stderr.write("[adapter] reread %s failed: %s\n" % (path, e))
            state, error = FAILED, str(e)[:200]
        with self.cond:
            self._set(path, state, error)
            self.cond.notify_all()

    def _loop(self):
        while True:
            with self.cond:
                while not self.queue:
                    self.cond.wait()
                path = self.queue.popleft()
                self._set(path, RUNNING)
            self._process(path)
