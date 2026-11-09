"""Login, sessions and identity tokens of a standalone box (E27 S3).

A login takes two steps:

1. `login(username, password)` checks the password and opens a short-lived
   challenge. A user with a TOTP secret is asked for a code (`step: totp`); a
   user without one -- first login, or after an administrator reset -- gets a
   fresh secret to enrol (`step: enroll`, with the otpauth:// URI for the QR
   code). There is no password-only login.
2. `complete_login(challenge, code)` checks the code and opens a session. The
   session secret goes into the browser cookie (set by the web UI server); its
   public handle is the `sid` claim.

With a session the web UI server asks `issue_token` for a short-lived HS256
JWT carrying the user's `sub`, `upn`, `groups`, `roles` and `drives`; the
adapter verifies it (ADAPTER_IDENTITY_MODE=require). Tokens are minted from the
current user record, so a change of roles or drives applies at the next token.

Failures: an unknown user, a wrong password and a locked account all answer
`invalid_credentials` after the same scrypt work, so neither the answer nor its
timing tells which usernames exist. Wrong passwords and wrong codes count
towards the account lockout; every failure also counts towards the client
throttle (password spraying across accounts from one machine).
"""
import collections
import re
import secrets
import threading
import time

import passwords
import tokens
import totp

ADMIN_ROLE = "admin"
STATUSES = ("active", "suspended")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,63}$")
ROLE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.@-]{0,127}$")
MAX_LIST_ITEMS = 64
MAX_DISPLAY_NAME = 128
MAX_CLIENTS_TRACKED = 10000


class AuthError(Exception):
    """A refused request. `code` is the stable error name for the caller."""

    def __init__(self, code, status=401):
        super().__init__(code)
        self.code = code
        self.status = status


def invalid_credentials():
    return AuthError("invalid_credentials", 401)


def normalize_username(raw):
    if not isinstance(raw, str):
        return None
    name = raw.strip().lower()
    return name if USERNAME_RE.match(name) else None


def _items(raw, pattern, field, lower):
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_LIST_ITEMS:
        raise AuthError("invalid_" + field, 400)
    result = []
    for item in raw:
        if not isinstance(item, str):
            raise AuthError("invalid_" + field, 400)
        item = item.strip().lower() if lower else item.strip()
        if not pattern.match(item):
            raise AuthError("invalid_" + field, 400)
        if item not in result:
            result.append(item)
    return result


def normalize_drive(raw):
    """An absolute drive path with a trailing slash. The adapter decides
    whether it lies under its drive prefix; this only rejects malformed and
    escaping paths."""
    if not isinstance(raw, str):
        raise AuthError("invalid_drives", 400)
    path = raw.strip()
    if not path.startswith("/") or "\\" in path or len(path) > 512 \
            or any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise AuthError("invalid_drives", 400)
    parts = [part for part in path.split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        raise AuthError("invalid_drives", 400)
    return "/" + "/".join(parts) + "/"


def normalize_drives(raw):
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_LIST_ITEMS:
        raise AuthError("invalid_drives", 400)
    result = []
    for item in raw:
        drive = normalize_drive(item)
        if drive not in result:
            result.append(drive)
    return result


def normalize_display_name(raw):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise AuthError("invalid_display_name", 400)
    name = raw.strip()
    if len(name) > MAX_DISPLAY_NAME or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise AuthError("invalid_display_name", 400)
    return name


class ClientThrottle:
    """Failed attempts per client address in a sliding window, in memory."""

    def __init__(self, max_failures, window_seconds, clock):
        self.max_failures = max_failures
        self.window = window_seconds
        self.clock = clock
        self._failures = collections.OrderedDict()
        self._lock = threading.Lock()

    def _recent(self, client, now):
        times = self._failures.get(client)
        if times is None:
            return None
        while times and times[0] <= now - self.window:
            times.popleft()
        if not times:
            del self._failures[client]
            return None
        return times

    def blocked(self, client):
        if not client:
            return False
        with self._lock:
            times = self._recent(client, self.clock())
            return times is not None and len(times) >= self.max_failures

    def failure(self, client):
        if not client:
            return
        with self._lock:
            now = self.clock()
            times = self._recent(client, now)
            if times is None:
                times = self._failures[client] = collections.deque()
            times.append(now)
            self._failures.move_to_end(client)
            while len(self._failures) > MAX_CLIENTS_TRACKED:
                self._failures.popitem(last=False)


def session_user(user):
    """What the web UI may know about the signed-in user."""
    return {"username": user["username"], "display_name": user["display_name"],
            "roles": list(user["roles"]), "groups": list(user["groups"]),
            "drives": list(user["drives"]),
            "must_change_password": user["must_change_password"]}


def admin_user(user, now):
    """What an administrator sees about a user: never a secret."""
    return {"username": user["username"], "display_name": user["display_name"],
            "source": user["source"], "roles": list(user["roles"]),
            "groups": list(user["groups"]), "drives": list(user["drives"]),
            "status": user["status"], "totp_enrolled": bool(user["totp_secret"]),
            "must_change_password": user["must_change_password"],
            "locked": user["locked_until"] > now,
            "locked_until": user["locked_until"] if user["locked_until"] > now else None,
            "created_at": user["created_at"], "password_changed_at": user["password_changed_at"],
            "last_login_at": user["last_login_at"]}


class Authenticator:
    def __init__(self, store, settings, jwt_secret, clock=time.time):
        if not isinstance(jwt_secret, bytes) or len(jwt_secret) < tokens.MIN_SECRET_BYTES:
            raise ValueError("the token signing key must be at least %d bytes" % tokens.MIN_SECRET_BYTES)
        self.store = store
        self.settings = settings
        self.jwt_secret = jwt_secret
        self.clock = clock
        self.throttle = ClientThrottle(settings.client_max_failures,
                                       settings.client_window_seconds, clock)

    # -- helpers ---------------------------------------------------------------
    def _event(self, event, username="", actor="", client="", detail=""):
        self.store.add_event(self.clock(), event, username, actor, client, detail)

    def _user_failure(self, user, client, reason):
        """Count a failed factor against the account. True when it locked it."""
        self.throttle.failure(client)
        now = self.clock()
        locked = self.store.add_failure(user["username"], self.settings.lockout_threshold,
                                        now + self.settings.lockout_seconds)
        self._event("login_failed", user["username"], client=client, detail=reason)
        if locked:
            self._event("account_locked", user["username"], client=client, detail=reason)
        return locked

    def _check_throttle(self, client):
        if self.throttle.blocked(client):
            self._event("client_throttled", client=client)
            raise AuthError("too_many_attempts", 429)

    def _session(self, secret):
        if not isinstance(secret, str) or not secret:
            raise AuthError("session_invalid")
        now = self.clock()
        session = self.store.get_session(secret)
        if session is None:
            raise AuthError("session_invalid")
        if session["expires_at"] <= now or session["last_seen"] <= now - self.settings.session_idle_seconds:
            self.store.delete_session(session["id"])
            raise AuthError("session_invalid")
        user = self.store.get_user(session["username"])
        if user is None or user["status"] != "active" or not user["totp_secret"]:
            self.store.delete_session(session["id"])
            raise AuthError("session_invalid")
        return session, user

    def _require_user(self, username):
        name = normalize_username(username)
        user = self.store.get_user(name) if name else None
        if user is None:
            raise AuthError("user_not_found", 404)
        return user

    # -- login -----------------------------------------------------------------
    def login(self, username, password, client=""):
        now = self.clock()
        self.store.purge_expired(now, self.settings.session_idle_seconds)
        self._check_throttle(client)
        name = normalize_username(username)
        user = self.store.get_user(name) if name else None
        if user is None or user["source"] != "local" or not user["password_hash"]:
            passwords.burn(password)
            self.throttle.failure(client)
            # The typed name is not logged: users sometimes type the password
            # into the username field.
            self._event("login_failed", client=client, detail="unknown_user")
            raise invalid_credentials()
        if user["locked_until"] > now:
            # The password is not checked while locked, or the lockout would
            # still let an attacker test guesses.
            passwords.burn(password)
            self.throttle.failure(client)
            self._event("login_failed", name, client=client, detail="locked")
            raise invalid_credentials()
        if not passwords.verify_password(password, user["password_hash"]):
            self._user_failure(user, client, "bad_password")
            raise invalid_credentials()
        if user["status"] != "active":
            self._event("login_failed", name, client=client, detail="suspended")
            raise invalid_credentials()
        if passwords.needs_rehash(user["password_hash"]):
            self.store.update_user(name, now, password_hash=passwords.hash_password(password))
        challenge = secrets.token_urlsafe(32)
        stage = "totp" if user["totp_secret"] else "enroll"
        pending = None if user["totp_secret"] else totp.new_secret()
        self.store.create_challenge(challenge, name, stage, pending,
                                    now + self.settings.challenge_seconds, client or "")
        self._event("login_password_ok", name, client=client, detail=stage)
        result = {"step": stage, "challenge": challenge,
                  "expires_in": self.settings.challenge_seconds}
        if pending:
            result["totp_secret"] = pending
            result["otpauth_uri"] = totp.provisioning_uri(pending, name, self.settings.totp_issuer)
        return result

    def complete_login(self, challenge, code, client=""):
        if not isinstance(challenge, str) or not challenge:
            raise AuthError("challenge_expired")
        self._check_throttle(client)
        now = self.clock()
        record = self.store.get_challenge(challenge)
        if record is None:
            raise AuthError("challenge_expired")
        if record["expires_at"] <= now:
            self.store.take_challenge(challenge)
            raise AuthError("challenge_expired")
        user = self.store.get_user(record["username"])
        if user is None or user["status"] != "active":
            self.store.take_challenge(challenge)
            raise AuthError("challenge_expired")
        if user["locked_until"] > now:
            self.store.take_challenge(challenge)
            raise AuthError("account_locked", 423)
        enrolling = record["stage"] == "enroll"
        # The account may have changed since the password step: enrolled by a
        # parallel login, or reset by an administrator.
        if enrolling == bool(user["totp_secret"]):
            self.store.take_challenge(challenge)
            raise AuthError("challenge_expired")
        if enrolling:
            secret, last_step = record["pending_secret"], None
        else:
            secret, last_step = user["totp_secret"], user["totp_last_step"]
        step = totp.match(secret, code, now, last_step)
        if step is None:
            self.store.count_challenge_attempt(challenge, self.settings.challenge_attempts)
            if self._user_failure(user, client, "bad_enrollment_code" if enrolling else "bad_totp"):
                raise AuthError("account_locked", 423)
            raise AuthError("invalid_code")
        if not self.store.take_challenge(challenge):
            raise AuthError("challenge_expired")
        name = user["username"]
        if enrolling:
            if not self.store.enroll_totp(name, secret, step, now):
                raise AuthError("challenge_expired")
            self._event("totp_enrolled", name, client=client)
        elif not self.store.accept_totp_step(name, step):
            self._user_failure(user, client, "totp_replay")
            raise AuthError("invalid_code")
        self.store.update_user(name, now, failed_count=0, last_login_at=now)
        session_id, secret_token = secrets.token_hex(16), secrets.token_urlsafe(32)
        expires_at = now + self.settings.session_max_seconds
        self.store.create_session(session_id, secret_token, name, now, expires_at, client or "")
        self._event("login_ok", name, client=client, detail="session " + session_id)
        user = self.store.get_user(name)
        return {"session": secret_token, "session_id": session_id, "expires_at": expires_at,
                "idle_seconds": self.settings.session_idle_seconds, "user": session_user(user),
                "password_change_required": user["must_change_password"]}

    # -- sessions --------------------------------------------------------------
    def me(self, session_secret):
        session, user = self._session(session_secret)
        self.store.touch_session(session["id"], self.clock())
        return {"user": session_user(user), "session_id": session["id"],
                "expires_at": session["expires_at"],
                "password_change_required": user["must_change_password"]}

    def issue_token(self, session_secret):
        session, user = self._session(session_secret)
        if user["must_change_password"]:
            raise AuthError("password_change_required", 403)
        now = self.clock()
        self.store.touch_session(session["id"], now)
        issued = int(now)
        expires = int(min(now + self.settings.token_ttl_seconds, session["expires_at"]))
        if expires <= issued:
            raise AuthError("session_invalid")
        claims = {"iss": self.settings.jwt_issuer, "aud": self.settings.jwt_audience,
                  "sub": user["username"], "upn": user["username"],
                  "name": user["display_name"], "groups": list(user["groups"]),
                  "roles": list(user["roles"]), "drives": list(user["drives"]),
                  "sid": session["id"], "iat": issued, "nbf": issued, "exp": expires,
                  "jti": secrets.token_hex(8)}
        return {"token": tokens.sign_hs256(claims, self.jwt_secret), "expires_at": expires,
                "user": session_user(user)}

    def change_password(self, session_secret, current, new, client=""):
        session, user = self._session(session_secret)
        self._check_throttle(client)
        name = user["username"]
        if not passwords.verify_password(current, user["password_hash"] or ""):
            if self._user_failure(user, client, "bad_password_change"):
                # Someone is guessing from inside a session: end them all.
                self.store.delete_user_sessions(name)
            raise AuthError("wrong_password", 403)
        problem = passwords.policy_problem(new, name, self.settings.password_min_length)
        if problem:
            raise AuthError(problem, 400)
        if new == current:
            raise AuthError("password_unchanged", 400)
        now = self.clock()
        self.store.update_user(name, now, password_hash=passwords.hash_password(new),
                               must_change_password=False, password_changed_at=now, failed_count=0)
        ended = self.store.delete_user_sessions(name, keep=session["id"])
        self._event("password_changed", name, actor=name, client=client,
                    detail="other sessions ended: %d" % ended)
        return {}

    def logout(self, session_secret, client=""):
        if isinstance(session_secret, str) and session_secret:
            session = self.store.get_session(session_secret)
            if session is not None:
                self.store.delete_session(session["id"])
                self._event("logout", session["username"], client=client,
                            detail="session " + session["id"])
        return {}

    # -- administration --------------------------------------------------------
    def admin_actor(self, session_secret):
        """The administrator behind a session, or AuthError."""
        session, user = self._session(session_secret)
        if user["must_change_password"]:
            raise AuthError("password_change_required", 403)
        if ADMIN_ROLE not in user["roles"]:
            raise AuthError("forbidden", 403)
        self.store.touch_session(session["id"], self.clock())
        return user["username"]

    def list_users(self):
        now = self.clock()
        return [admin_user(user, now) for user in self.store.list_users()]

    def create_user(self, actor, username, password, display_name="", roles=(), groups=(),
                    drives=(), temporary=True, client=""):
        name = normalize_username(username)
        if name is None:
            raise AuthError("invalid_username", 400)
        problem = passwords.policy_problem(password, name, self.settings.password_min_length)
        if problem:
            raise AuthError(problem, 400)
        fields = {"display_name": normalize_display_name(display_name),
                  "roles": _items(roles, ROLE_RE, "roles", True),
                  "groups": _items(groups, GROUP_RE, "groups", False),
                  "drives": normalize_drives(drives)}
        now = self.clock()
        if not self.store.insert_user(name, now, source="local",
                                      password_hash=passwords.hash_password(password),
                                      must_change_password=bool(temporary),
                                      password_changed_at=now, **fields):
            raise AuthError("user_exists", 409)
        self._event("user_created", name, actor=actor, client=client,
                    detail="roles=%s" % ",".join(fields["roles"]))
        return admin_user(self.store.get_user(name), now)

    def update_user(self, actor, username, display_name=None, roles=None, groups=None,
                    drives=None, status=None, client=""):
        user = self._require_user(username)
        fields = {}
        if display_name is not None:
            fields["display_name"] = normalize_display_name(display_name)
        if roles is not None:
            fields["roles"] = _items(roles, ROLE_RE, "roles", True)
        if groups is not None:
            fields["groups"] = _items(groups, GROUP_RE, "groups", False)
        if drives is not None:
            fields["drives"] = normalize_drives(drives)
        if status is not None:
            if status not in STATUSES:
                raise AuthError("invalid_status", 400)
            fields["status"] = status
        now = self.clock()
        if self.store.update_user(user["username"], now, keep_admin=True, **fields) is None:
            raise AuthError("last_admin", 409)
        if fields.get("status") == "suspended":
            self.store.delete_user_sessions(user["username"])
        self._event("user_updated", user["username"], actor=actor, client=client,
                    detail="fields=%s" % ",".join(sorted(fields)))
        return admin_user(self.store.get_user(user["username"]), now)

    def delete_user(self, actor, username, client=""):
        user = self._require_user(username)
        if self.store.delete_user(user["username"], keep_admin=True) is None:
            raise AuthError("last_admin", 409)
        self._event("user_deleted", user["username"], actor=actor, client=client)
        return {}

    def reset_totp(self, actor, username, client=""):
        """Drop the user's authenticator: the next login enrols a new one.
        Every session of the user ends."""
        user = self._require_user(username)
        now = self.clock()
        self.store.update_user(user["username"], now, totp_secret=None, totp_last_step=None)
        ended = self.store.delete_user_sessions(user["username"])
        self._event("totp_reset", user["username"], actor=actor, client=client,
                    detail="sessions ended: %d" % ended)
        return admin_user(self.store.get_user(user["username"]), now)

    def unlock(self, actor, username, client=""):
        user = self._require_user(username)
        now = self.clock()
        self.store.update_user(user["username"], now, failed_count=0, locked_until=0)
        self._event("account_unlocked", user["username"], actor=actor, client=client)
        return admin_user(self.store.get_user(user["username"]), now)

    def reset_password(self, actor, username, password, temporary=True, client=""):
        user = self._require_user(username)
        problem = passwords.policy_problem(password, user["username"], self.settings.password_min_length)
        if problem:
            raise AuthError(problem, 400)
        now = self.clock()
        self.store.update_user(user["username"], now, password_hash=passwords.hash_password(password),
                               must_change_password=bool(temporary), password_changed_at=now,
                               failed_count=0, locked_until=0)
        ended = self.store.delete_user_sessions(user["username"])
        self._event("password_reset", user["username"], actor=actor, client=client,
                    detail="sessions ended: %d" % ended)
        return admin_user(self.store.get_user(user["username"]), now)

    def list_events(self, limit=200, username=None):
        return self.store.list_events(limit, normalize_username(username) if username else None)
