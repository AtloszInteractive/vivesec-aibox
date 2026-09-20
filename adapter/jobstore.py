"""Persistent, scope-isolated background job storage for the adapter."""
import json
import os
import threading
import time

from session import session_key


_TERMINAL = ("done", "error", "cancelled", "interrupted")


class JobStore:
    """Atomic JSON job store keyed by the (VVS-User, VVS-Drive) scope."""

    def __init__(self, root=None, retention_days=7, max_per_user=50):
        self.root = root or None
        self.retention_seconds = max(0, float(retention_days)) * 86400
        self.max_per_user = max(1, int(max_per_user))
        self._lock = threading.RLock()
        self._events = {}
        if self.root:
            try:
                os.makedirs(self.root, exist_ok=True)
                self.recover_interrupted()
            except OSError:
                # LUKS-backed /data may not be mounted until the box unlocks.
                pass

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        return cls(
            root=(env.get("ADAPTER_JOBS_DIR", "") or "").strip() or None,
            retention_days=float(env.get("ADAPTER_JOB_RETENTION_DAYS", "7") or 7),
            max_per_user=int(env.get("ADAPTER_JOB_MAX_PER_USER", "50") or 50),
        )

    def enabled(self):
        return bool(self.root)

    def _scope_dir(self, user, drive):
        return os.path.join(self.root, session_key(user, drive))

    @staticmethod
    def _safe_id(job_id):
        """The id arrives from the client, so it must stay one file name inside
        the scope directory -- never a path that escapes it."""
        name = str(job_id or "")
        if not name or name.startswith(".") or os.path.basename(name) != name \
                or "/" in name or "\\" in name:
            raise ValueError("invalid job_id")
        return name

    def _path(self, user, drive, job_id):
        return os.path.join(self._scope_dir(user, drive), self._safe_id(job_id) + ".json")

    def _write(self, path, job):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(job, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)

    @staticmethod
    def _read(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def create(self, job_id, user, drive, request):
        if not self.root:
            raise RuntimeError("job store disabled (ADAPTER_JOBS_DIR empty)")
        now = time.time()
        job = {
            "job_id": job_id, "user": user or "", "drive": drive or "",
            "status": "queued", "created": now, "started": None,
            "finished": None, "seen_ts": None, "request": dict(request),
            "code": None, "result": None, "error": None,
        }
        with self._lock:
            self._write(self._path(user, drive, job_id), job)
            self._events[job_id] = threading.Event()
            self.sweep()
        return dict(job)

    def get(self, user, drive, job_id):
        if not self.root or not job_id:
            return None
        with self._lock:
            job = self._read(self._path(user, drive, job_id))
            if not job or job.get("user") != (user or "") or job.get("drive") != (drive or ""):
                return None
            return job

    def update(self, user, drive, job_id, **changes):
        with self._lock:
            path = self._path(user, drive, job_id)
            job = self._read(path)
            if not job or job.get("user") != (user or "") or job.get("drive") != (drive or ""):
                return None
            job.update(changes)
            self._write(path, job)
            if job.get("status") in _TERMINAL:
                self._events.setdefault(job_id, threading.Event()).set()
            return dict(job)

    def start(self, user, drive, job_id):
        return self.update(user, drive, job_id, status="running", started=time.time())

    def finish(self, user, drive, job_id, code, result):
        return self.update(
            user, drive, job_id,
            status="cancelled" if code == 499 else ("error" if code >= 400 else "done"), code=code,
            result=result, error=(result or {}).get("error") if isinstance(result, dict) else None,
            finished=time.time(),
        )

    def progress(self, user, drive, job_id, chars=None, tokens=None):
        changes = {"progress_chars": int(chars or 0)}
        if tokens is not None:
            changes["progress_tokens"] = int(tokens)
        return self.update(user, drive, job_id, **changes)

    def wait(self, user, drive, job_id, timeout):
        job = self.get(user, drive, job_id)
        if not job or job.get("status") in _TERMINAL:
            return job
        event = self._events.setdefault(job_id, threading.Event())
        event.wait(timeout)
        return self.get(user, drive, job_id)

    def list(self, user, drive):
        if not self.root:
            return []
        directory = self._scope_dir(user, drive)
        try:
            paths = [os.path.join(directory, name) for name in os.listdir(directory)
                     if name.endswith(".json")]
        except OSError:
            return []
        with self._lock:
            jobs = [self._read(path) for path in paths]
        jobs = [job for job in jobs if job and job.get("user") == (user or "")
                and job.get("drive") == (drive or "")]
        jobs.sort(key=lambda job: job.get("created", 0), reverse=True)
        return jobs

    def mark_seen(self, user, drive, job_id):
        return self.update(user, drive, job_id, seen_ts=time.time())

    def cancel_queued(self, user, drive, job_id):
        with self._lock:
            path = self._path(user, drive, job_id)
            job = self._read(path)
            if not job or job.get("user") != (user or "") or job.get("drive") != (drive or ""):
                return None
            if job.get("status") != "queued":
                return job
            job.update(status="cancelled", finished=time.time(),
                       error="cancelled by user")
            self._write(path, job)
            self._events.setdefault(job_id, threading.Event()).set()
            return dict(job)

    def request_cancel(self, user, drive, job_id):
        return self.update(user, drive, job_id, cancel_requested=True)

    def delete(self, user, drive, job_id):
        """Drop one finished job from the scope. Returns "deleted", "active"
        when the job is still queued or running (removing the file would leave
        the worker writing into nothing), or None when the caller's scope has
        no such job -- unknown rather than forbidden, like the other handlers.
        """
        if not self.root or not job_id:
            return None
        with self._lock:
            path = self._path(user, drive, job_id)
            job = self._read(path)
            if not job or job.get("user") != (user or "") or job.get("drive") != (drive or ""):
                return None
            if job.get("status") not in _TERMINAL:
                return "active"
            os.remove(path)
            self._events.pop(job_id, None)
            return "deleted"

    def recover_interrupted(self):
        if not self.root:
            return 0
        changed = 0
        with self._lock:
            for base, _dirs, files in os.walk(self.root):
                for name in files:
                    if not name.endswith(".json"):
                        continue
                    path = os.path.join(base, name)
                    job = self._read(path)
                    if job and job.get("status") in ("queued", "running"):
                        job.update(status="interrupted", finished=time.time(),
                                   error="adapter restarted before the job completed")
                        self._write(path, job)
                        changed += 1
        return changed

    def sweep(self):
        if not self.root:
            return 0
        now = time.time()
        removed = 0
        with self._lock:
            by_user = {}
            for base, _dirs, files in os.walk(self.root):
                for name in files:
                    if not name.endswith(".json"):
                        continue
                    path = os.path.join(base, name)
                    job = self._read(path)
                    if not job:
                        continue
                    by_user.setdefault(job.get("user", ""), []).append((path, job))
            for jobs in by_user.values():
                jobs.sort(key=lambda item: item[1].get("created", 0), reverse=True)
                for index, (path, job) in enumerate(jobs):
                    expired = (self.retention_seconds > 0
                               and now - job.get("created", now) > self.retention_seconds)
                    terminal = job.get("status") in _TERMINAL
                    if terminal and (expired or index >= self.max_per_user):
                        try:
                            os.remove(path)
                            self._events.pop(job.get("job_id"), None)
                            removed += 1
                        except OSError:
                            pass
        return removed

    def purge(self):
        if not self.root:
            return 0
        removed = 0
        with self._lock:
            for base, dirs, files in os.walk(self.root, topdown=False):
                for name in files:
                    if name.endswith(".json") or name.endswith(".tmp"):
                        try:
                            os.remove(os.path.join(base, name))
                            removed += 1
                        except OSError:
                            pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(base, name))
                    except OSError:
                        pass
            self._events.clear()
        return removed

    def stats(self):
        count = 0
        if self.root:
            for _base, _dirs, files in os.walk(self.root):
                count += len([name for name in files if name.endswith(".json")])
        return {"enabled": bool(self.root), "jobs": count,
                "retention_days": self.retention_seconds / 86400,
                "max_per_user": self.max_per_user}