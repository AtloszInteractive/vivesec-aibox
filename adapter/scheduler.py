"""Single-worker FIFO scheduler for heavyweight adapter jobs."""
import collections
import threading


class QueueFull(Exception):
    pass


class HeavyScheduler:
    """Run one heavy job at a time while keeping submission non-blocking."""

    def __init__(self, worker, max_queued_per_user=5, autostart=True):
        self.worker = worker
        self.max_queued_per_user = max(1, int(max_queued_per_user))
        self._queue = collections.deque()
        self._condition = threading.Condition()
        self._running = None
        self._stopped = False
        self._thread = None
        if autostart:
            self.start()

    def start(self):
        with self._condition:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="adapter-heavy-worker")
            self._thread.start()

    def submit(self, job_id, user, drive, args):
        item = {"job_id": job_id, "user": user or "", "drive": drive or "",
                "args": tuple(args), "cancel_event": threading.Event()}
        with self._condition:
            queued = sum(1 for current in self._queue
                         if current["user"] == item["user"])
            if queued >= self.max_queued_per_user:
                raise QueueFull("too many queued background jobs for this user")
            self._queue.append(item)
            position = len(self._queue)
            self._condition.notify()
            return position

    def cancel(self, job_id, user, drive):
        """Remove a queued job. Return queued, running, or missing."""
        user = user or ""
        drive = drive or ""
        with self._condition:
            for item in self._queue:
                if (item["job_id"] == job_id and item["user"] == user
                        and item["drive"] == drive):
                    self._queue.remove(item)
                    return "queued"
            if (self._running and self._running["job_id"] == job_id
                    and self._running["user"] == user
                    and self._running["drive"] == drive):
                self._running["cancel_event"].set()
                return "running"
            return "missing"

    def position(self, job_id, user, drive):
        user = user or ""
        drive = drive or ""
        with self._condition:
            for position, item in enumerate(self._queue, 1):
                if (item["job_id"] == job_id and item["user"] == user
                        and item["drive"] == drive):
                    return position
            return 0 if (self._running and self._running["job_id"] == job_id) else None

    def stop(self):
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                while not self._queue and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                item = self._queue.popleft()
                self._running = item
            try:
                self.worker(*item["args"], cancel_event=item["cancel_event"])
            finally:
                with self._condition:
                    self._running = None
                    self._condition.notify_all()