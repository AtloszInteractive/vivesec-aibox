"""SQLite store of the authentication service: users, sessions, login
challenges and the event log.

Secrets that grant access (session and challenge tokens) are stored as SHA-256
hashes only, so a copy of the database does not let anyone in. The TOTP
secrets have to be stored as they are; the database is created 0600 and, on a
production box, lives on the encrypted volume (E05).

One connection serialised by a lock: the service is small and the writes are
short. The steps that must not race -- consuming a challenge, accepting a TOTP
time step -- are single conditional statements that report whether they won.
"""
import hashlib
import json
import os
import sqlite3
import threading

SCHEMA_VERSION = 1
ADMIN_ROLE = "admin"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'local',
    password_hash TEXT,
    roles TEXT NOT NULL DEFAULT '[]',
    user_groups TEXT NOT NULL DEFAULT '[]',
    drives TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    must_change_password INTEGER NOT NULL DEFAULT 0,
    totp_secret TEXT,
    totp_last_step INTEGER,
    failed_count INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    password_changed_at REAL,
    last_login_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires_at REAL NOT NULL,
    client TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions (username);
CREATE TABLE IF NOT EXISTS challenges (
    secret_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    stage TEXT NOT NULL,
    pending_secret TEXT,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    client TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    event TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    client TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
"""

_LIST_FIELDS = {"roles": "roles", "groups": "user_groups", "drives": "drives"}
_PLAIN_FIELDS = ("display_name", "source", "password_hash", "status", "must_change_password",
                 "totp_secret", "totp_last_step", "failed_count", "locked_until",
                 "password_changed_at", "last_login_at")


def secret_hash(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _user(row):
    if row is None:
        return None
    user = dict(row)
    for field, column in _LIST_FIELDS.items():
        user[field] = json.loads(user.pop(column) or "[]")
    user["must_change_password"] = bool(user["must_change_password"])
    return user


class Store:
    def __init__(self, path):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if not os.path.exists(path):
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)",
                               (str(SCHEMA_VERSION),))

    def close(self):
        with self._lock:
            self._conn.close()

    def _one(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def _all(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _write(self, sql, args=()):
        with self._lock, self._conn:
            return self._conn.execute(sql, args).rowcount

    # -- users -----------------------------------------------------------------
    def get_user(self, username):
        return _user(self._one("SELECT * FROM users WHERE username = ?", (username,)))

    def list_users(self):
        return [_user(row) for row in self._all("SELECT * FROM users ORDER BY username")]

    def insert_user(self, username, now, **fields):
        columns, values = ["username", "created_at", "updated_at"], [username, now, now]
        for field, value in fields.items():
            column, value = self._column(field, value)
            columns.append(column)
            values.append(value)
        try:
            self._write("INSERT INTO users (%s) VALUES (%s)"
                        % (", ".join(columns), ", ".join("?" * len(values))), values)
        except sqlite3.IntegrityError:
            return False
        return True

    def update_user(self, username, now, keep_admin=False, **fields):
        """Update a user. With `keep_admin` the change is refused (None) when it
        would leave no active administrator; the count and the change share one
        transaction, so parallel requests cannot both pass the check."""
        sets, values = ["updated_at = ?"], [now]
        for field, value in fields.items():
            column, value = self._column(field, value)
            sets.append("%s = ?" % column)
            values.append(value)
        values.append(username)
        with self._lock, self._conn:
            if keep_admin and not self._keeps_an_admin(username, fields.get("roles"), fields.get("status")):
                return None
            return self._conn.execute("UPDATE users SET %s WHERE username = ?" % ", ".join(sets),
                                      values).rowcount == 1

    @staticmethod
    def _column(field, value):
        if field in _LIST_FIELDS:
            return _LIST_FIELDS[field], json.dumps(list(value))
        if field in _PLAIN_FIELDS:
            return field, int(value) if field == "must_change_password" else value
        raise ValueError("unknown user field %r" % field)

    def _keeps_an_admin(self, username, roles=None, status=None, deleted=False):
        """Whether an active administrator remains after changing `username`.
        Call with the lock held. A box without any administrator stays allowed
        to change (the first one is created from the shell)."""
        before = after = 0
        for row in self._conn.execute("SELECT username, roles, status FROM users"):
            current_roles, current_status = json.loads(row["roles"] or "[]"), row["status"]
            before += ADMIN_ROLE in current_roles and current_status == "active"
            if row["username"] == username:
                if deleted:
                    continue
                current_roles = current_roles if roles is None else list(roles)
                current_status = current_status if status is None else status
            after += ADMIN_ROLE in current_roles and current_status == "active"
        return after > 0 or before == 0

    def delete_user(self, username, keep_admin=False):
        """Delete a user with their sessions; None when `keep_admin` refuses."""
        with self._lock, self._conn:
            if keep_admin and not self._keeps_an_admin(username, deleted=True):
                return None
            self._conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
            self._conn.execute("DELETE FROM challenges WHERE username = ?", (username,))
            return self._conn.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount == 1

    def add_failure(self, username, threshold, lock_until):
        """Count one failed attempt; at `threshold` lock the account until
        `lock_until` and restart the count. True when this locked it."""
        with self._lock, self._conn:
            row = self._conn.execute("SELECT failed_count FROM users WHERE username = ?",
                                     (username,)).fetchone()
            if row is None:
                return False
            count = row["failed_count"] + 1
            if count >= threshold:
                self._conn.execute("UPDATE users SET failed_count = 0, locked_until = ? "
                                   "WHERE username = ?", (lock_until, username))
                self._conn.execute("DELETE FROM challenges WHERE username = ?", (username,))
                return True
            self._conn.execute("UPDATE users SET failed_count = ? WHERE username = ?",
                               (count, username))
            return False

    def accept_totp_step(self, username, step):
        """Record `step` as used; False when it (or a later one) already was."""
        return self._write("UPDATE users SET totp_last_step = ? WHERE username = ? AND "
                           "totp_secret IS NOT NULL AND (totp_last_step IS NULL OR totp_last_step < ?)",
                           (step, username, step)) == 1

    def enroll_totp(self, username, secret, step, now):
        """Store the confirmed TOTP secret; False when one is already set."""
        return self._write("UPDATE users SET totp_secret = ?, totp_last_step = ?, updated_at = ? "
                           "WHERE username = ? AND totp_secret IS NULL",
                           (secret, step, now, username)) == 1

    # -- sessions --------------------------------------------------------------
    def create_session(self, session_id, secret, username, now, expires_at, client):
        self._write("INSERT INTO sessions (id, secret_hash, username, created_at, last_seen, "
                    "expires_at, client) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, secret_hash(secret), username, now, now, expires_at, client))

    def get_session(self, secret):
        row = self._one("SELECT * FROM sessions WHERE secret_hash = ?", (secret_hash(secret),))
        return dict(row) if row else None

    def touch_session(self, session_id, now):
        self._write("UPDATE sessions SET last_seen = ? WHERE id = ?", (now, session_id))

    def delete_session(self, session_id):
        return self._write("DELETE FROM sessions WHERE id = ?", (session_id,)) == 1

    def delete_user_sessions(self, username, keep=None):
        return self._write("DELETE FROM sessions WHERE username = ? AND id != ?",
                           (username, keep or ""))

    def count_sessions(self, username):
        return self._one("SELECT COUNT(*) AS n FROM sessions WHERE username = ?", (username,))["n"]

    # -- login challenges ------------------------------------------------------
    def create_challenge(self, secret, username, stage, pending_secret, expires_at, client):
        with self._lock, self._conn:
            # One open login per user: a new password step replaces the old one.
            self._conn.execute("DELETE FROM challenges WHERE username = ?", (username,))
            self._conn.execute("INSERT INTO challenges (secret_hash, username, stage, pending_secret, "
                               "expires_at, client) VALUES (?, ?, ?, ?, ?, ?)",
                               (secret_hash(secret), username, stage, pending_secret, expires_at, client))

    def get_challenge(self, secret):
        row = self._one("SELECT * FROM challenges WHERE secret_hash = ?", (secret_hash(secret),))
        return dict(row) if row else None

    def count_challenge_attempt(self, secret, limit):
        """One more wrong code; the challenge is dropped at `limit`."""
        with self._lock, self._conn:
            key = secret_hash(secret)
            self._conn.execute("UPDATE challenges SET attempts = attempts + 1 WHERE secret_hash = ?", (key,))
            self._conn.execute("DELETE FROM challenges WHERE secret_hash = ? AND attempts >= ?", (key, limit))

    def take_challenge(self, secret):
        """Consume a challenge; False when it was already used or dropped."""
        return self._write("DELETE FROM challenges WHERE secret_hash = ?", (secret_hash(secret),)) == 1

    def purge_expired(self, now, idle_seconds):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM challenges WHERE expires_at <= ?", (now,))
            self._conn.execute("DELETE FROM sessions WHERE expires_at <= ? OR last_seen <= ?",
                               (now, now - idle_seconds))

    # -- events ----------------------------------------------------------------
    def add_event(self, now, event, username="", actor="", client="", detail=""):
        self._write("INSERT INTO events (at, event, username, actor, client, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (now, event, username or "", actor or "",
                                                  client or "", detail or ""))

    def list_events(self, limit=200, username=None):
        limit = max(1, min(int(limit), 1000))
        if username:
            rows = self._all("SELECT * FROM events WHERE username = ? ORDER BY id DESC LIMIT ?",
                             (username, limit))
        else:
            rows = self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]
