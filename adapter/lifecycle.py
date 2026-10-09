"""User lifecycle events: deletion, suspension and revocation (E02).

The ViVeSecBox (fed by the customer's directory) tells the AI Box when a user
is deleted, suspended or loses access; the AI Box only receives and enforces.
A locked user's requests are refused and their threads, jobs and generated
files become unreachable. Nothing is deleted here: retention and erasure stay
separate decisions.

  deleted     -- terminal; a later `reinstated` is refused (409);
  suspended   -- locked until `reinstated`;
  revoked     -- AI Box access withdrawn; locked until `reinstated`;
  reinstated  -- lifts a suspension or revocation.

The state is a small JSON file written atomically. A file that exists but
cannot be parsed makes the store UNAVAILABLE rather than empty: forgetting who
was locked would silently reopen their data, so callers refuse service until
an operator repairs it.
"""
import json
import os
import threading
import time

EVENT_DELETED = "deleted"
EVENT_SUSPENDED = "suspended"
EVENT_REVOKED = "revoked"
EVENT_REINSTATED = "reinstated"
LOCK_EVENTS = (EVENT_DELETED, EVENT_SUSPENDED, EVENT_REVOKED)
EVENTS = LOCK_EVENTS + (EVENT_REINSTATED,)

MAX_ID_LENGTH = 512
SCHEMA = 1


class LifecycleError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class LifecycleUnavailable(Exception):
    """The persisted state cannot be read; nobody's status is known."""


def _clean_id(value, name):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise LifecycleError("%s must be a string" % name)
    value = value.strip()
    if len(value) > MAX_ID_LENGTH:
        raise LifecycleError("%s too long" % name)
    return value or None


class LifecycleStore:
    def __init__(self, path=None, clock=time.time):
        self.path = path or None
        self._clock = clock
        self._lock = threading.Lock()
        self._users = {}
        self._corrupt = None
        self._load()

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        return cls((env.get("ADAPTER_LIFECYCLE_PATH") or "").strip() or None)

    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            self._corrupt = "lifecycle state unreadable: %s" % e
            return
        users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(users, dict) or any(
                not isinstance(v, dict) or v.get("state") not in LOCK_EVENTS
                for v in users.values()):
            self._corrupt = "lifecycle state malformed"
            return
        self._users = {str(k): dict(v) for k, v in users.items()}

    def _persist(self, users):
        if not self.path:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"schema": SCHEMA, "users": users}, handle,
                      ensure_ascii=False, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def state_of(self, keys):
        """The lock state ('deleted'/'suspended'/'revoked') of the first locked
        key, None when none is locked. Raises LifecycleUnavailable."""
        with self._lock:
            if self._corrupt:
                raise LifecycleUnavailable(self._corrupt)
            for key in keys:
                entry = self._users.get(key) if key else None
                if entry:
                    return entry["state"]
            return None

    def apply(self, event, user, upn=None, reason=None, event_id=None):
        event = (event or "").strip().lower() if isinstance(event, str) else ""
        if event not in EVENTS:
            raise LifecycleError("unknown lifecycle event %r (expected one of %s)"
                                 % (event, ", ".join(EVENTS)))
        user = _clean_id(user, "user")
        if not user:
            raise LifecycleError("missing user")
        upn = _clean_id(upn, "upn")
        reason = _clean_id(reason, "reason")
        event_id = _clean_id(event_id, "event_id")
        keys = [k for k in dict.fromkeys((user, upn)) if k]
        with self._lock:
            if self._corrupt:
                raise LifecycleUnavailable(self._corrupt)
            users = {k: dict(v) for k, v in self._users.items()}
            if event == EVENT_REINSTATED:
                if any(users.get(k, {}).get("state") == EVENT_DELETED for k in keys):
                    raise LifecycleError("a deleted user cannot be reinstated", status=409)
                for key in keys:
                    users.pop(key, None)
                self._persist(users)
                self._users = users
                return {"user": user, "upn": upn, "state": "active"}
            entry = {"state": event, "ts": self._clock(), "reason": reason,
                     "event_id": event_id}
            state = event
            for key in keys:
                if users.get(key, {}).get("state") == EVENT_DELETED:
                    state = EVENT_DELETED
            entry["state"] = state
            for key in keys:
                users[key] = dict(entry, user=user)
            # Lock first, persist second: if the write fails the user stays
            # locked for the life of this process instead of staying open.
            self._users = users
            self._persist(users)
            return {"user": user, "upn": upn, "state": state}

    def purge(self):
        """Factory reset: the user data the locks protected is gone too."""
        with self._lock:
            if self.path:
                try:
                    os.remove(self.path)
                except FileNotFoundError:
                    pass
            self._users = {}
            self._corrupt = None

    def stats(self):
        with self._lock:
            states = {}
            for _user, state in {(e.get("user"), e["state"]) for e in self._users.values()}:
                states[state] = states.get(state, 0) + 1
            return {"persistent": bool(self.path), "unavailable": bool(self._corrupt),
                    "locked": states}
