"""Password hashing for local users (E27 S3): scrypt from the standard library.

A stored hash is self-describing -- `scrypt$N$r$p$salt$digest` (unpadded
base64) -- so the cost can be raised later and old hashes still verify; a
successful login with an outdated hash rehashes it (`needs_rehash`).

scrypt is memory-hard on purpose (32 MiB per hash at the default cost). The box
has unified memory shared with the models, so at most `MAX_PARALLEL` hashes run
at a time; further logins wait for a slot.
"""
import base64
import hashlib
import hmac
import secrets
import threading

SCHEME = "scrypt"
N = 2 ** 15
R = 8
P = 1
DKLEN = 32
SALT_BYTES = 16
MAX_PASSWORD_BYTES = 1024
MAX_PARALLEL = 2
# Upper bounds for parameters read back from a stored hash.
_MAX_N = 2 ** 20
_MAX_R = 32
_MAX_P = 16

_slots = threading.BoundedSemaphore(MAX_PARALLEL)
_dummy = {}


def _b64(data):
    return base64.b64encode(data).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)


def _derive(password, salt, n, r, p, dklen):
    with _slots:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                              maxmem=256 * r * n + 1024 * 1024, dklen=dklen)


def hash_password(password):
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, N, R, P, DKLEN)
    return "%s$%d$%d$%d$%s$%s" % (SCHEME, N, R, P, _b64(salt), _b64(digest))


def _parse(encoded):
    scheme, n, r, p, salt, digest = encoded.split("$")
    n, r, p = int(n), int(r), int(p)
    if scheme != SCHEME or not (2 <= n <= _MAX_N) or n & (n - 1) or not (1 <= r <= _MAX_R) \
            or not (1 <= p <= _MAX_P):
        raise ValueError("unsupported hash parameters")
    salt, digest = _unb64(salt), _unb64(digest)
    if len(salt) < 8 or len(digest) < 16:
        raise ValueError("hash too short")
    return n, r, p, salt, digest


def verify_password(password, encoded):
    """True when `password` matches `encoded`. A malformed hash never matches.
    An unusable password costs the same work as a wrong one, so the answer
    time does not tell whether the account exists."""
    if not isinstance(password, str) or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        burn("")
        return False
    try:
        n, r, p, salt, digest = _parse(encoded)
    except (AttributeError, TypeError, ValueError):
        return False
    try:
        candidate = _derive(password, salt, n, r, p, len(digest))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(candidate, digest)


def needs_rehash(encoded):
    try:
        n, r, p, _salt, digest = _parse(encoded)
    except (AttributeError, TypeError, ValueError):
        return True
    return (n, r, p, len(digest)) != (N, R, P, DKLEN)


def burn(password):
    """Spend the time a real verification takes, for an unknown or locked user,
    so the answer time does not tell which usernames exist."""
    key = (N, R, P)
    if key not in _dummy:
        _dummy[key] = hash_password(secrets.token_hex(16))
    candidate = password if isinstance(password, str) else ""
    if len(candidate.encode("utf-8")) > MAX_PASSWORD_BYTES:
        candidate = ""
    verify_password(candidate, _dummy[key])


def policy_problem(password, username, min_length):
    """Why `password` is not acceptable as a new password, or None."""
    if not isinstance(password, str) or len(password) < min_length:
        return "password_too_short"
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return "password_too_long"
    if any(ord(char) < 32 or ord(char) == 127 for char in password):
        return "password_invalid_characters"
    if password.strip().lower() == (username or "").strip().lower():
        return "password_same_as_username"
    if len(set(password)) < 4:
        return "password_too_simple"
    return None
